<?php
/**
 * Plugin Name: AI Content Chatbot
 * Description: Standalone RAG chatbot for WordPress content. Trains from pages, posts and public custom post types without sitemap crawling.
 * Version: 0.7.0
 * Author: Local
 * Requires at least: 6.2
 * Requires PHP: 8.0
 * Text Domain: ai-content-chatbot
 */

if (!defined('ABSPATH')) {
    exit;
}

final class AICB_Plugin {
    private const OPTION_KEY = 'aicb_settings';
    private const FAQ_OPTION_KEY = 'aicb_faqs';
    private const WIDGET_OPTION_KEY = 'aicb_widget_config';
    private const VERSION_OPTION = 'aicb_version';
    // Feingranulare Inhaltsauswahl fuer das Training.
    private const INDEX_MODE_OPTION = 'aicb_index_mode';        // 'all' | 'selected'
    private const SELECTED_POSTS_OPTION = 'aicb_selected_posts'; // array<int> Post-IDs (nur publish)
    private const SELECTED_PDFS_OPTION = 'aicb_selected_pdfs';   // array<int> Attachment-IDs (PDF)

    /**
     * Standard-Logo des Assistenten. Zeichnet sich in der Farbe des Avatars
     * (currentColor), laesst sich im Widget-Tab durch ein eigenes SVG oder ein
     * Emoji ersetzen.
     */
    private const DEFAULT_ICON_SVG = '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M12 3c-4.97 0-9 3.36-9 7.5 0 2.3 1.25 4.35 3.2 5.72-.13 1.3-.6 2.5-1.4 3.5-.2.26-.02.64.31.6 1.9-.2 3.6-.9 4.98-1.98.62.1 1.26.16 1.91.16 4.97 0 9-3.36 9-7.5S16.97 3 12 3z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><circle cx="8.25" cy="10.5" r="1.15" fill="currentColor"/><circle cx="12" cy="10.5" r="1.15" fill="currentColor"/><circle cx="15.75" cy="10.5" r="1.15" fill="currentColor"/></svg>';
    private const REST_NS = 'ai-content-chatbot/v1';
    private const ASSET_VERSION = '0.7.0';
    // Cosinus-Aehnlichkeit: darunter gilt ein Treffer als themenfremd.
    private const CONTEXT_MIN_SCORE = 0.18;
    private const CARD_MIN_SCORE = 0.28;
    // Kleinere Chunks finden Details praeziser, die Ueberlappung haelt
    // Zusammenhaenge ueber die Grenze hinweg zusammen.
    private const CHUNK_TARGET_TOKENS = 380;
    private const CHUNK_OVERLAP_TOKENS = 70;
    private const SESSION_TTL = 86400;

    private static ?AICB_Plugin $instance = null;

    public static function instance(): AICB_Plugin {
        if (self::$instance === null) {
            self::$instance = new self();
        }
        return self::$instance;
    }

    private function __construct() {
        add_action('init', [$this, 'maybe_upgrade'], 5);
        add_action('init', [$this, 'register_shortcodes']);
        add_action('admin_menu', [$this, 'register_admin_menu']);
        add_action('admin_enqueue_scripts', [$this, 'enqueue_admin_assets']);
        add_action('wp_enqueue_scripts', [$this, 'enqueue_widget_assets']);
        add_action('wp_footer', [$this, 'render_footer_widget']);
        add_action('rest_api_init', [$this, 'register_rest_routes']);
        add_action('save_post', [$this, 'schedule_post_reindex'], 20, 3);
        add_action('aicb_reindex_single_post', [$this, 'cron_reindex_single_post']);
    }

    public static function activate(): void {
        global $wpdb;
        require_once ABSPATH . 'wp-admin/includes/upgrade.php';

        $charset = $wpdb->get_charset_collate();
        $chunks = $wpdb->prefix . 'aicb_chunks';
        $sessions = $wpdb->prefix . 'aicb_sessions';
        $events = $wpdb->prefix . 'aicb_events';

        dbDelta("CREATE TABLE {$chunks} (
            id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
            source_id varchar(191) NOT NULL,
            source_type varchar(64) NOT NULL,
            source_url text NULL,
            title text NULL,
            section text NULL,
            content longtext NOT NULL,
            content_hash char(40) NOT NULL,
            embedding longtext NULL,
            token_estimate int(11) NOT NULL DEFAULT 0,
            updated_at datetime NOT NULL,
            PRIMARY KEY  (id),
            KEY source_id (source_id),
            KEY source_type (source_type),
            KEY content_hash (content_hash)
        ) {$charset};");

        dbDelta("CREATE TABLE {$sessions} (
            id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
            token_hash char(64) NOT NULL,
            expires_at datetime NOT NULL,
            created_at datetime NOT NULL,
            last_seen_at datetime NOT NULL,
            ip_hash char(64) NULL,
            user_agent varchar(255) NULL,
            messages int(11) NOT NULL DEFAULT 0,
            PRIMARY KEY  (id),
            UNIQUE KEY token_hash (token_hash),
            KEY expires_at (expires_at)
        ) {$charset};");

        dbDelta("CREATE TABLE {$events} (
            id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
            session_hash char(64) NULL,
            user_id bigint(20) unsigned NULL,
            question longtext NOT NULL,
            answer longtext NULL,
            status varchar(32) NOT NULL DEFAULT 'ok',
            error text NULL,
            input_tokens int(11) NOT NULL DEFAULT 0,
            output_tokens int(11) NOT NULL DEFAULT 0,
            model varchar(96) NULL,
            feedback tinyint(4) NOT NULL DEFAULT 0,
            created_at datetime NOT NULL,
            PRIMARY KEY  (id),
            KEY created_at (created_at),
            KEY status (status),
            KEY user_id (user_id),
            KEY feedback (feedback)
        ) {$charset};");

        if (!get_option(self::OPTION_KEY)) {
            add_option(self::OPTION_KEY, self::default_settings());
        }
        if (!get_option(self::FAQ_OPTION_KEY)) {
            add_option(self::FAQ_OPTION_KEY, []);
        }
        if (!get_option(self::WIDGET_OPTION_KEY)) {
            add_option(self::WIDGET_OPTION_KEY, self::default_widget_config());
        }
    }

    public static function deactivate(): void {
        wp_clear_scheduled_hook('aicb_reindex_single_post');
    }

    public static function default_settings(): array {
        return [
            'openai_api_key' => '',
            'chat_model' => 'gpt-4o-mini',
            'embedding_model' => 'text-embedding-3-large',
            'retriever_k' => 12,
            'max_context_chars' => 20000,
            'batch_size' => 4,
            'auto_index_on_save' => true,
            'widget_enabled' => true,
            'enabled_post_types' => [],
            'include_excerpts' => true,
            'include_taxonomies' => true,
            'privacy_url' => '',
            'contact_url' => '',
            'contact_email' => get_option('admin_email'),
            'contact_phone' => '',
            'system_prompt' => self::default_system_prompt(),
        ];
    }

    public static function default_system_prompt(): string {
        return "Du bist der Assistent dieser Website und antwortest zu Inhalten nur auf Basis des bereitgestellten Kontexts.\n"
            . "Wenn etwas nicht im Kontext steht, sage ehrlich, dass du es nicht weisst.\n"
            . "Antworte praezise, nenne konkrete Fakten und gib Quellen als direkte URLs aus.";
    }

    /**
     * Oberflaechentexte pro Sprache. Genutzt wird das Paket der Seitensprache;
     * im Admin gesetzte Texte haben Vorrang. Unbekannte Sprachen fallen auf
     * Englisch zurueck.
     */
    private const LANG_PACKS = [
        'de' => [
            'title' => 'Haben Sie Fragen?',
            'status' => 'Antwortet sofort',
            'intro' => 'Hallo! Ich finde gerne eine direkte Antwort fuer dich.',
            'topics_label' => 'Beliebte Themen',
            'placeholder' => 'Schreibe deine Frage ...',
            'disclaimer' => 'Der Assistent kann Fehler machen. Bitte pruefe wichtige Informationen.',
            'privacy_label' => 'Datenschutz',
            'greeting' => 'Hallo! Wie kann ich helfen?',
            'action_contact' => 'Kontakt aufnehmen',
            'action_email' => 'E-Mail schreiben',
            'action_details' => 'Mehr Details',
            'action_details_q' => 'Kannst du das genauer erklaeren?',
            'steps' => ['Denke nach ...', 'Suche im Index ...', 'Formuliere Antwort ...'],
            'error' => 'Es ist ein Fehler aufgetreten: ',
            'sources' => 'Quellen',
            'aria_minimize' => 'Chat minimieren',
            'aria_close' => 'Chat schliessen',
            'aria_send' => 'Nachricht senden',
            'aria_open' => 'Chat oeffnen',
            'aria_teaser_close' => 'Hinweis schliessen',
            'no_index' => 'Ich bin noch nicht auf die Inhalte dieser Website trainiert. Zu allgemeinen Fragen helfe ich dir aber gerne weiter.',
        ],
        'en' => [
            'title' => 'Any questions?',
            'status' => 'Replies instantly',
            'intro' => 'Hi! I am happy to find a direct answer for you.',
            'topics_label' => 'Popular topics',
            'placeholder' => 'Type your question ...',
            'disclaimer' => 'The assistant can make mistakes. Please verify important information.',
            'privacy_label' => 'Privacy',
            'greeting' => 'Hi! How can I help?',
            'action_contact' => 'Get in touch',
            'action_email' => 'Send an email',
            'action_details' => 'More details',
            'action_details_q' => 'Can you explain that in more detail?',
            'steps' => ['Thinking ...', 'Searching index ...', 'Drafting answer ...'],
            'error' => 'Something went wrong: ',
            'sources' => 'Sources',
            'aria_minimize' => 'Minimize chat',
            'aria_close' => 'Close chat',
            'aria_send' => 'Send message',
            'aria_open' => 'Open chat',
            'aria_teaser_close' => 'Dismiss notice',
            'no_index' => 'I have not been trained on this website yet. I am still happy to help with general questions.',
        ],
        'fr' => [
            'title' => 'Des questions ?',
            'status' => 'Repond immediatement',
            'intro' => 'Bonjour ! Je trouve volontiers une reponse directe pour vous.',
            'topics_label' => 'Sujets populaires',
            'placeholder' => 'Ecrivez votre question ...',
            'disclaimer' => "L'assistant peut se tromper. Merci de verifier les informations importantes.",
            'privacy_label' => 'Confidentialite',
            'greeting' => 'Bonjour ! Comment puis-je aider ?',
            'action_contact' => 'Nous contacter',
            'action_email' => 'Envoyer un e-mail',
            'action_details' => 'Plus de details',
            'action_details_q' => 'Peux-tu expliquer plus en detail ?',
            'steps' => ['Je reflechis ...', 'Je cherche dans l\'index ...', 'Je formule la reponse ...'],
            'error' => 'Une erreur est survenue : ',
            'sources' => 'Sources',
            'aria_minimize' => 'Reduire le chat',
            'aria_close' => 'Fermer le chat',
            'aria_send' => 'Envoyer le message',
            'aria_open' => 'Ouvrir le chat',
            'aria_teaser_close' => 'Fermer la notification',
            'no_index' => "Je ne suis pas encore entraine sur le contenu de ce site. Je peux tout de meme repondre a des questions generales.",
        ],
        'es' => [
            'title' => '¿Tienes preguntas?',
            'status' => 'Responde al instante',
            'intro' => '¡Hola! Con gusto te doy una respuesta directa.',
            'topics_label' => 'Temas populares',
            'placeholder' => 'Escribe tu pregunta ...',
            'disclaimer' => 'El asistente puede equivocarse. Verifica la informacion importante.',
            'privacy_label' => 'Privacidad',
            'greeting' => '¡Hola! ¿Como puedo ayudar?',
            'action_contact' => 'Contactar',
            'action_email' => 'Enviar un correo',
            'action_details' => 'Mas detalles',
            'action_details_q' => '¿Puedes explicarlo con mas detalle?',
            'steps' => ['Pensando ...', 'Buscando en el indice ...', 'Redactando respuesta ...'],
            'error' => 'Se ha producido un error: ',
            'sources' => 'Fuentes',
            'aria_minimize' => 'Minimizar el chat',
            'aria_close' => 'Cerrar el chat',
            'aria_send' => 'Enviar mensaje',
            'aria_open' => 'Abrir el chat',
            'aria_teaser_close' => 'Cerrar el aviso',
            'no_index' => 'Todavia no estoy entrenado con el contenido de esta web. Aun asi puedo ayudarte con preguntas generales.',
        ],
        'it' => [
            'title' => 'Hai domande?',
            'status' => 'Risponde subito',
            'intro' => 'Ciao! Trovo volentieri una risposta diretta per te.',
            'topics_label' => 'Argomenti frequenti',
            'placeholder' => 'Scrivi la tua domanda ...',
            'disclaimer' => "L'assistente puo sbagliare. Verifica le informazioni importanti.",
            'privacy_label' => 'Privacy',
            'greeting' => 'Ciao! Come posso aiutare?',
            'action_contact' => 'Contattaci',
            'action_email' => 'Invia una email',
            'action_details' => 'Piu dettagli',
            'action_details_q' => 'Puoi spiegarlo piu in dettaglio?',
            'steps' => ['Sto pensando ...', 'Cerco nell\'indice ...', 'Formulo la risposta ...'],
            'error' => 'Si e verificato un errore: ',
            'sources' => 'Fonti',
            'aria_minimize' => 'Riduci la chat',
            'aria_close' => 'Chiudi la chat',
            'aria_send' => 'Invia messaggio',
            'aria_open' => 'Apri la chat',
            'aria_teaser_close' => 'Chiudi la notifica',
            'no_index' => 'Non sono ancora addestrato sui contenuti di questo sito. Posso comunque aiutarti con domande generali.',
        ],
        'nl' => [
            'title' => 'Heb je vragen?',
            'status' => 'Antwoordt direct',
            'intro' => 'Hallo! Ik vind graag een direct antwoord voor je.',
            'topics_label' => 'Populaire onderwerpen',
            'placeholder' => 'Schrijf je vraag ...',
            'disclaimer' => 'De assistent kan fouten maken. Controleer belangrijke informatie.',
            'privacy_label' => 'Privacy',
            'greeting' => 'Hallo! Hoe kan ik helpen?',
            'action_contact' => 'Contact opnemen',
            'action_email' => 'E-mail sturen',
            'action_details' => 'Meer details',
            'action_details_q' => 'Kun je dat uitgebreider uitleggen?',
            'steps' => ['Aan het nadenken ...', 'Zoeken in de index ...', 'Antwoord opstellen ...'],
            'error' => 'Er is een fout opgetreden: ',
            'sources' => 'Bronnen',
            'aria_minimize' => 'Chat minimaliseren',
            'aria_close' => 'Chat sluiten',
            'aria_send' => 'Bericht verzenden',
            'aria_open' => 'Chat openen',
            'aria_teaser_close' => 'Melding sluiten',
            'no_index' => 'Ik ben nog niet getraind op de inhoud van deze site. Met algemene vragen help ik je graag.',
        ],
        'pt' => [
            'title' => 'Tem perguntas?',
            'status' => 'Responde na hora',
            'intro' => 'Ola! Encontro com gosto uma resposta direta para voce.',
            'topics_label' => 'Topicos populares',
            'placeholder' => 'Escreva a sua pergunta ...',
            'disclaimer' => 'O assistente pode errar. Verifique informacoes importantes.',
            'privacy_label' => 'Privacidade',
            'greeting' => 'Ola! Como posso ajudar?',
            'action_contact' => 'Entrar em contacto',
            'action_email' => 'Enviar e-mail',
            'action_details' => 'Mais detalhes',
            'action_details_q' => 'Podes explicar com mais detalhe?',
            'steps' => ['A pensar ...', 'A procurar no indice ...', 'A redigir a resposta ...'],
            'error' => 'Ocorreu um erro: ',
            'sources' => 'Fontes',
            'aria_minimize' => 'Minimizar o chat',
            'aria_close' => 'Fechar o chat',
            'aria_send' => 'Enviar mensagem',
            'aria_open' => 'Abrir o chat',
            'aria_teaser_close' => 'Fechar o aviso',
            'no_index' => 'Ainda nao fui treinado com o conteudo deste site. Mesmo assim posso ajudar com perguntas gerais.',
        ],
        'tr' => [
            'title' => 'Sorunuz mu var?',
            'status' => 'Hemen yanitlar',
            'intro' => 'Merhaba! Size dogrudan bir yanit bulmaktan memnuniyet duyarim.',
            'topics_label' => 'Populer konular',
            'placeholder' => 'Sorunuzu yazin ...',
            'disclaimer' => 'Asistan hata yapabilir. Onemli bilgileri lutfen kontrol edin.',
            'privacy_label' => 'Gizlilik',
            'greeting' => 'Merhaba! Nasil yardimci olabilirim?',
            'action_contact' => 'Iletisime gec',
            'action_email' => 'E-posta gonder',
            'action_details' => 'Daha fazla detay',
            'action_details_q' => 'Bunu daha ayrintili anlatabilir misin?',
            'steps' => ['Dusunuyorum ...', 'Dizinde ariyorum ...', 'Yaniti hazirliyorum ...'],
            'error' => 'Bir hata olustu: ',
            'sources' => 'Kaynaklar',
            'aria_minimize' => 'Sohbeti kucult',
            'aria_close' => 'Sohbeti kapat',
            'aria_send' => 'Mesaj gonder',
            'aria_open' => 'Sohbeti ac',
            'aria_teaser_close' => 'Bildirimi kapat',
            'no_index' => 'Bu sitenin icerigi icin henuz egitilmedim. Genel sorularda yine de yardimci olabilirim.',
        ],
        'pl' => [
            'title' => 'Masz pytania?',
            'status' => 'Odpowiada natychmiast',
            'intro' => 'Czesc! Chetnie znajde dla Ciebie bezposrednia odpowiedz.',
            'topics_label' => 'Popularne tematy',
            'placeholder' => 'Napisz swoje pytanie ...',
            'disclaimer' => 'Asystent moze sie mylic. Sprawdz wazne informacje.',
            'privacy_label' => 'Prywatnosc',
            'greeting' => 'Czesc! Jak moge pomoc?',
            'action_contact' => 'Kontakt',
            'action_email' => 'Wyslij e-mail',
            'action_details' => 'Wiecej szczegolow',
            'action_details_q' => 'Czy mozesz to wyjasnic dokladniej?',
            'steps' => ['Mysle ...', 'Szukam w indeksie ...', 'Formuluje odpowiedz ...'],
            'error' => 'Wystapil blad: ',
            'sources' => 'Zrodla',
            'aria_minimize' => 'Zminimalizuj czat',
            'aria_close' => 'Zamknij czat',
            'aria_send' => 'Wyslij wiadomosc',
            'aria_open' => 'Otworz czat',
            'aria_teaser_close' => 'Zamknij powiadomienie',
            'no_index' => 'Nie zostalem jeszcze wytrenowany na tresci tej strony. Chetnie pomoge w ogolnych pytaniach.',
        ],
        'ru' => [
            'title' => 'Есть вопросы?',
            'status' => 'Отвечает сразу',
            'intro' => 'Здравствуйте! Я с радостью найду для вас точный ответ.',
            'topics_label' => 'Популярные темы',
            'placeholder' => 'Напишите свой вопрос ...',
            'disclaimer' => 'Ассистент может ошибаться. Проверяйте важную информацию.',
            'privacy_label' => 'Конфиденциальность',
            'greeting' => 'Здравствуйте! Чем могу помочь?',
            'action_contact' => 'Связаться',
            'action_email' => 'Написать письмо',
            'action_details' => 'Подробнее',
            'action_details_q' => 'Можешь объяснить подробнее?',
            'steps' => ['Думаю ...', 'Ищу в индексе ...', 'Формулирую ответ ...'],
            'error' => 'Произошла ошибка: ',
            'sources' => 'Источники',
            'aria_minimize' => 'Свернуть чат',
            'aria_close' => 'Закрыть чат',
            'aria_send' => 'Отправить сообщение',
            'aria_open' => 'Открыть чат',
            'aria_teaser_close' => 'Закрыть уведомление',
            'no_index' => 'Я ещё не обучен на содержимом этого сайта. С общими вопросами я всё равно помогу.',
        ],
        'ar' => [
            'title' => 'هل لديك أسئلة؟',
            'status' => 'يرد فوراً',
            'intro' => 'مرحباً! يسعدني أن أجد لك إجابة مباشرة.',
            'topics_label' => 'مواضيع شائعة',
            'placeholder' => 'اكتب سؤالك ...',
            'disclaimer' => 'قد يخطئ المساعد. يرجى التحقق من المعلومات المهمة.',
            'privacy_label' => 'الخصوصية',
            'greeting' => 'مرحباً! كيف أساعدك؟',
            'action_contact' => 'تواصل معنا',
            'action_email' => 'إرسال بريد',
            'action_details' => 'مزيد من التفاصيل',
            'action_details_q' => 'هل يمكنك التوضيح بمزيد من التفصيل؟',
            'steps' => ['أفكر ...', 'أبحث في الفهرس ...', 'أصوغ الإجابة ...'],
            'error' => 'حدث خطأ: ',
            'sources' => 'المصادر',
            'aria_minimize' => 'تصغير المحادثة',
            'aria_close' => 'إغلاق المحادثة',
            'aria_send' => 'إرسال الرسالة',
            'aria_open' => 'فتح المحادثة',
            'aria_teaser_close' => 'إغلاق التنبيه',
            'no_index' => 'لم أتدرب بعد على محتوى هذا الموقع. لكن يسعدني مساعدتك في الأسئلة العامة.',
        ],
    ];

    /** Sprachen mit Schreibrichtung von rechts nach links. */
    private const RTL_LANGS = ['ar', 'he', 'fa', 'ur', 'ps', 'sd', 'yi'];

    private static function normalize_lang(string $value): string {
        $clean = strtolower(trim($value));
        if ($clean === '') {
            return '';
        }
        $clean = str_replace('_', '-', $clean);
        return explode('-', $clean)[0];
    }

    /** Sprache der aktuellen Seite - bei WPML/Polylang pro Seite korrekt. */
    private function site_lang(): string {
        $lang = self::normalize_lang((string) get_bloginfo('language'));
        return $lang !== '' ? $lang : 'en';
    }

    private function lang_pack(string $lang): array {
        $key = self::normalize_lang($lang);
        return self::LANG_PACKS[$key] ?? self::LANG_PACKS['en'];
    }

    private function is_rtl_lang(string $lang): bool {
        return in_array(self::normalize_lang($lang), self::RTL_LANGS, true);
    }

    /** Alle Quellen-Bezeichnungen, damit der Quellenblock in jeder Sprache erkannt wird. */
    /**
     * Sprache der Nutzernachricht erkennen. Das Modell alleine entscheidet das
     * unzuverlaessig, sobald Verlauf und Kontext in einer anderen Sprache
     * stehen - dann antwortet es in der Sprache der Website statt in der des
     * Nutzers. Deshalb wird die Sprache hier bestimmt und vorgegeben.
     */
    private const LANG_NAMES = [
        'de' => 'German (Deutsch)', 'en' => 'English', 'tr' => 'Turkish (Türkçe)',
        'ar' => 'Arabic (العربية)', 'fr' => 'French (Français)', 'es' => 'Spanish (Español)',
        'it' => 'Italian (Italiano)', 'nl' => 'Dutch (Nederlands)', 'pt' => 'Portuguese (Português)',
        'pl' => 'Polish (Polski)', 'ru' => 'Russian (Русский)', 'el' => 'Greek (Ελληνικά)',
        'he' => 'Hebrew (עברית)', 'uk' => 'Ukrainian (Українська)', 'zh' => 'Chinese (中文)',
        'ja' => 'Japanese (日本語)', 'ko' => 'Korean (한국어)', 'hi' => 'Hindi (हिन्दी)',
    ];

    // Schriftsysteme sind eindeutig - wer arabisch schreibt, will arabisch lesen.
    private const SCRIPT_PATTERNS = [
        'ar' => '/[\x{0600}-\x{06FF}\x{0750}-\x{077F}]/u',
        'he' => '/[\x{0590}-\x{05FF}]/u',
        'ru' => '/[\x{0400}-\x{04FF}]/u',
        'el' => '/[\x{0370}-\x{03FF}]/u',
        'hi' => '/[\x{0900}-\x{097F}]/u',
        'ja' => '/[\x{3040}-\x{30FF}]/u',
        'ko' => '/[\x{AC00}-\x{D7AF}]/u',
        'zh' => '/[\x{4E00}-\x{9FFF}]/u',
    ];

    // Haeufige Funktionswoerter. Kurze Nachrichten entscheidet oft ein einziges
    // Wort ("merhaba", "danke"), deshalb sind Gruesse mit aufgenommen.
    private const LANG_STOPWORDS = [
        'de' => ['der', 'die', 'das', 'und', 'ist', 'sind', 'ich', 'du', 'ihr', 'wir', 'nicht', 'wie', 'was', 'wo', 'wann', 'warum', 'kann', 'koennen', 'können', 'habt', 'haben', 'hat', 'mit', 'von', 'für', 'fuer', 'auf', 'eine', 'einen', 'mehr', 'gibt', 'bitte', 'danke', 'hallo', 'guten', 'tag', 'preis', 'preise', 'kosten', 'zimmer'],
        'en' => ['the', 'is', 'are', 'how', 'what', 'where', 'when', 'why', 'can', 'you', 'your', 'we', 'do', 'does', 'have', 'has', 'please', 'thanks', 'thank', 'yes', 'with', 'for', 'about', 'more', 'there', 'hi', 'hello', 'hey', 'price', 'prices', 'cost', 'opening', 'hours'],
        'tr' => ['bir', 'və', 've', 'için', 'icin', 'nasıl', 'nasil', 'var', 'yok', 'nerede', 'zaman', 'merhaba', 'selam', 'teşekkür', 'tesekkur', 'evet', 'hayır', 'hayir', 'ile', 'daha', 'çok', 'cok', 'mı', 'mi', 'mu', 'mü', 'fiyat', 'fiyatlar', 'oda', 'saat'],
        'fr' => ['le', 'la', 'les', 'des', 'une', 'est', 'vous', 'je', 'nous', 'comment', 'quel', 'quelle', 'pour', 'avec', 'merci', 'oui', 'non', 'plus', 'bonjour', 'salut', 'prix', 'ouvert'],
        'es' => ['el', 'los', 'las', 'una', 'usted', 'como', 'cómo', 'cual', 'cuál', 'para', 'con', 'gracias', 'sí', 'hola', 'más', 'mas', 'donde', 'dónde', 'precio', 'precios'],
        'it' => ['il', 'lo', 'gli', 'le', 'una', 'come', 'quale', 'per', 'con', 'grazie', 'sì', 'più', 'piu', 'dove', 'ciao', 'buongiorno', 'prezzo', 'prezzi', 'camera', 'sono'],
        'nl' => ['het', 'een', 'hoe', 'wat', 'waar', 'kan', 'jij', 'jullie', 'met', 'voor', 'dank', 'bedankt', 'nee', 'meer', 'hallo', 'prijs', 'prijzen', 'kamer', 'openingstijden'],
        'pt' => ['os', 'as', 'uma', 'como', 'qual', 'para', 'com', 'obrigado', 'obrigada', 'sim', 'não', 'nao', 'mais', 'onde', 'olá', 'ola', 'preço', 'preco', 'quarto'],
        'pl' => ['jak', 'co', 'gdzie', 'czy', 'nie', 'tak', 'dla', 'jest', 'są', 'sa', 'dziękuję', 'dziekuje', 'cześć', 'czesc', 'dzień', 'dobry', 'cena', 'ceny', 'pokój', 'pokoj'],
    ];

    // Diakritika als Zusatzsignal - "ı" und "ğ" gibt es praktisch nur im Tuerkischen.
    private const LANG_HINT_CHARS = [
        'tr' => ['ı', 'ş', 'ğ'],
        'de' => ['ä', 'ö', 'ü', 'ß'],
        'fr' => ['é', 'è', 'ê', 'à', 'ç'],
        'es' => ['ñ', '¿', '¡'],
        'pt' => ['ã', 'õ'],
        'pl' => ['ą', 'ć', 'ę', 'ł', 'ń', 'ś', 'ź', 'ż'],
        'it' => ['à', 'ò'],
    ];

    private function detect_message_lang(string $text): string {
        $raw = trim($text);
        if ($raw === '') {
            return '';
        }
        foreach (self::SCRIPT_PATTERNS as $code => $pattern) {
            $hits = preg_match_all($pattern, $raw);
            if ($hits && $hits >= 3) {
                return $code;
            }
        }

        $lower = $this->str_lower($raw);
        preg_match_all('/[\p{L}]+/u', $lower, $found);
        $tokens = array_unique($found[0] ?? []);
        if (!$tokens) {
            return '';
        }

        $scores = [];
        foreach (self::LANG_STOPWORDS as $code => $words) {
            $score = (float) count(array_intersect($tokens, $words));
            foreach (self::LANG_HINT_CHARS[$code] ?? [] as $char) {
                if (mb_strpos($lower, $char) !== false) {
                    $score += 1.5;
                }
            }
            if ($score > 0) {
                $scores[$code] = $score;
            }
        }
        if (!$scores) {
            return '';
        }
        arsort($scores);
        $codes = array_keys($scores);
        $best = $scores[$codes[0]];
        $second = isset($codes[1]) ? $scores[$codes[1]] : 0.0;
        // Bei Gleichstand lieber nichts sagen als falsch raten.
        if ($best < 1 || ($best - $second) < 0.5) {
            return '';
        }
        return $codes[0];
    }

    private function lang_display_name(string $code): string {
        $key = self::normalize_lang($code);
        return self::LANG_NAMES[$key] ?? ($key !== '' ? strtoupper($key) : "the user's language");
    }

    private function sources_labels(): array {
        $labels = ['Quellen', 'Quelle', 'Sources', 'Source'];
        foreach (self::LANG_PACKS as $pack) {
            $labels[] = $pack['sources'];
        }
        return array_values(array_unique($labels));
    }

    /** Beschriftungen der Feedback-Leiste (👍/👎) je Sprache, Fallback Englisch. */
    private function feedback_labels(string $lang): array {
        $map = [
            'de' => ['question' => 'War das hilfreich?', 'yes' => 'Hilfreich', 'no' => 'Nicht hilfreich', 'thanks' => 'Danke fuer dein Feedback!'],
            'en' => ['question' => 'Was this helpful?', 'yes' => 'Helpful', 'no' => 'Not helpful', 'thanks' => 'Thanks for your feedback!'],
            'fr' => ['question' => 'Cela vous a-t-il aide ?', 'yes' => 'Utile', 'no' => 'Pas utile', 'thanks' => 'Merci pour votre retour !'],
            'es' => ['question' => 'Te resulto util?', 'yes' => 'Util', 'no' => 'No util', 'thanks' => 'Gracias por tu opinion!'],
            'it' => ['question' => 'E stato utile?', 'yes' => 'Utile', 'no' => 'Non utile', 'thanks' => 'Grazie per il feedback!'],
            'nl' => ['question' => 'Was dit nuttig?', 'yes' => 'Nuttig', 'no' => 'Niet nuttig', 'thanks' => 'Bedankt voor je feedback!'],
            'pt' => ['question' => 'Isto foi util?', 'yes' => 'Util', 'no' => 'Nao util', 'thanks' => 'Obrigado pelo seu feedback!'],
            'tr' => ['question' => 'Bu yardimci oldu mu?', 'yes' => 'Yardimci', 'no' => 'Yardimci degil', 'thanks' => 'Geri bildirimin icin tesekkurler!'],
            'pl' => ['question' => 'Czy to bylo pomocne?', 'yes' => 'Pomocne', 'no' => 'Niepomocne', 'thanks' => 'Dziekujemy za opinie!'],
            'ru' => ['question' => 'Это было полезно?', 'yes' => 'Полезно', 'no' => 'Не полезно', 'thanks' => 'Спасибо за отзыв!'],
            'ar' => ['question' => 'هل كان هذا مفيدا؟', 'yes' => 'مفيد', 'no' => 'غير مفيد', 'thanks' => 'شكرا على ملاحظاتك!'],
        ];
        return $map[$lang] ?? $map['en'];
    }

    public static function default_widget_config(): array {
        return [
            'theme' => [
                'accent' => '#8c8875',
                'accentStrong' => '#756f5f',
                'statusDot' => '#4f8a5b',
                'launcherBg' => '#8c8875',
                'bg' => '#f9f6f1',
                'panel' => '#ffffff',
                'text' => '#2f2a24',
                'avatarBg' => '#3a352c',
                'avatarFg' => '#f8f6f1',
                'userBubble' => '#ece5da',
                'botBubble' => '#ffffff',
                'composerBg' => '#ffffff',
                'composerBorder' => '#e8e2d8',
                'composerButtonBg' => '#3a352c',
                'composerButtonText' => '#f8f6f1',
            ],
            // Leere Texte werden automatisch aus dem Sprachpaket der Seite gefuellt.
            'copy' => [
                'icon' => self::DEFAULT_ICON_SVG,
                'title' => '',
                'status' => '',
                'intro' => '',
                'topics_label' => '',
                'placeholder' => '',
                'disclaimer' => '',
                'privacy_label' => '',
            ],
            'greeting' => [
                'enabled' => true,
                'text' => '',
                'delay_ms' => 1200,
            ],
            'topics' => [
                ['label' => 'Leistungen', 'question' => 'Welche Leistungen bietet ihr an?', 'url' => '', 'highlight' => true],
                ['label' => 'Preise', 'question' => 'Was kostet das?', 'url' => '', 'highlight' => false],
                ['label' => 'Kontakt', 'question' => 'Wie kann ich Kontakt aufnehmen?', 'url' => '', 'highlight' => false],
            ],
        ];
    }

    /**
     * Update-Migration. Beim Sprung auf 0.3.0 wuerden zwei Altlasten die neue
     * Sprachlogik aushebeln: der auf Deutsch festgenagelte System-Prompt und die
     * deutschen Standardtexte im Widget. Beides wird nur ersetzt, wenn es noch
     * unveraendert dem alten Standard entspricht - eigene Texte bleiben.
     */
    public function maybe_upgrade(): void {
        if ((string) get_option(self::VERSION_OPTION, '') === self::ASSET_VERSION) {
            return;
        }

        $legacy_prompt = "Du bist ein Assistent, der nur auf Basis des bereitgestellten WordPress-Kontexts antwortet.\n"
            . "Wenn etwas nicht im Kontext steht, sage ehrlich, dass du es nicht weisst.\n"
            . "Antworte praezise auf Deutsch, nenne konkrete Fakten und gib Quellen als direkte URLs aus.";
        $settings = $this->settings();
        $settings_changed = false;
        if (trim((string) ($settings['system_prompt'] ?? '')) === trim($legacy_prompt)) {
            $settings['system_prompt'] = self::default_system_prompt();
            $settings_changed = true;
        }
        // Alte Standardwerte anheben - mehr Kontext heisst mehr Details in der
        // Antwort. Selbst gesetzte Werte bleiben unangetastet.
        if ((int) ($settings['retriever_k'] ?? 0) === 8) {
            $settings['retriever_k'] = 12;
            $settings_changed = true;
        }
        if ((int) ($settings['max_context_chars'] ?? 0) === 14000) {
            $settings['max_context_chars'] = 20000;
            $settings_changed = true;
        }
        if ($settings_changed) {
            update_option(self::OPTION_KEY, $settings, false);
        }

        $legacy_copy = [
            'title' => ['Haben Sie Fragen?'],
            'status' => ['Antwortet sofort'],
            'intro' => ['Hallo! Ich finde gerne eine direkte Antwort fuer dich.'],
            'topics_label' => ['Beliebte Themen'],
            'placeholder' => ['Schreibe deine Frage ...', 'Frage schreiben...'],
            'disclaimer' => ['Der Assistent kann Fehler machen. Bitte pruefe wichtige Informationen.'],
            'privacy_label' => ['Datenschutz'],
        ];
        $widget = get_option(self::WIDGET_OPTION_KEY, null);
        if (is_array($widget)) {
            $changed = false;
            foreach ($legacy_copy as $key => $values) {
                $current = trim((string) ($widget['copy'][$key] ?? ''));
                if ($current !== '' && in_array($current, $values, true)) {
                    $widget['copy'][$key] = '';
                    $changed = true;
                }
            }
            if (trim((string) ($widget['greeting']['text'] ?? '')) === 'Hallo! Wie kann ich helfen?') {
                $widget['greeting']['text'] = '';
                $changed = true;
            }
            // Emoji-Standard durch das Logo ersetzen; eigene Icons bleiben.
            if (trim((string) ($widget['copy']['icon'] ?? '')) === '💬') {
                $widget['copy']['icon'] = self::DEFAULT_ICON_SVG;
                $changed = true;
            }
            if ($changed) {
                update_option(self::WIDGET_OPTION_KEY, $widget, false);
            }
        }

        // Migration: feedback-Spalte fuer 👍/👎 nachziehen (idempotent).
        global $wpdb;
        $events = $wpdb->prefix . 'aicb_events';
        $has_feedback = $wpdb->get_var($wpdb->prepare(
            "SHOW COLUMNS FROM {$events} LIKE %s",
            'feedback'
        ));
        if (!$has_feedback) {
            $wpdb->query("ALTER TABLE {$events} ADD COLUMN feedback tinyint(4) NOT NULL DEFAULT 0, ADD KEY feedback (feedback)");
        }

        update_option(self::VERSION_OPTION, self::ASSET_VERSION, false);
    }

    public function register_shortcodes(): void {
        add_shortcode('ai_content_chatbot', [$this, 'shortcode_widget']);
    }

    public function register_admin_menu(): void {
        add_menu_page(
            'AI Chatbot',
            'AI Chatbot',
            'manage_options',
            'ai-content-chatbot',
            [$this, 'render_admin_page'],
            'dashicons-format-chat',
            58
        );
    }

    public function enqueue_admin_assets(string $hook): void {
        if ($hook !== 'toplevel_page_ai-content-chatbot') {
            return;
        }
        $base = plugin_dir_url(__FILE__);
        // Mediathek-Dialog (wp.media) fuer die PDF-Auswahl im Inhalte-Tab.
        wp_enqueue_media();
        wp_enqueue_style('aicb-admin', $base . 'assets/admin.css', [], self::ASSET_VERSION);

        // Die Live-Vorschau im Widget-Tab nutzt exakt die Frontend-Assets und
        // dasselbe Markup - so zeigt sie wirklich das, was Besucher sehen.
        wp_enqueue_style('aicb-widget', $base . 'assets/widget.css', [], self::ASSET_VERSION);
        wp_enqueue_script('aicb-widget', $base . 'assets/widget.js', [], self::ASSET_VERSION, true);
        wp_localize_script('aicb-widget', 'AICBWidget', [
            'restUrl' => esc_url_raw(rest_url(self::REST_NS . '/')),
            'config' => $this->public_widget_config(),
        ]);

        wp_enqueue_script('aicb-admin', $base . 'assets/admin.js', ['aicb-widget'], self::ASSET_VERSION, true);
        wp_localize_script('aicb-admin', 'AICBAdmin', [
            'restUrl' => esc_url_raw(rest_url(self::REST_NS . '/')),
            'nonce' => wp_create_nonce('wp_rest'),
            'siteUrl' => home_url('/'),
            'previewHtml' => $this->widget_shell_markup('inline'),
            'widgetConfig' => $this->public_widget_config(),
            // Sprachpaket der Seite: fuellt leere Felder in der Vorschau genauso
            // wie spaeter im Frontend.
            'copyDefaults' => $this->lang_pack($this->site_lang()),
        ]);
    }

    public function enqueue_widget_assets(): void {
        if (!$this->setting_bool('widget_enabled', true)) {
            return;
        }
        $this->enqueue_widget_assets_now();
    }

    private function enqueue_widget_assets_now(): void {
        $base = plugin_dir_url(__FILE__);
        wp_enqueue_style('aicb-widget', $base . 'assets/widget.css', [], self::ASSET_VERSION);
        wp_enqueue_script('aicb-widget', $base . 'assets/widget.js', [], self::ASSET_VERSION, true);
        wp_localize_script('aicb-widget', 'AICBWidget', [
            'restUrl' => esc_url_raw(rest_url(self::REST_NS . '/')),
            'config' => $this->public_widget_config(),
        ]);
    }

    public function render_admin_page(): void {
        if (!current_user_can('manage_options')) {
            wp_die(esc_html__('You do not have permission to access this page.', 'ai-content-chatbot'));
        }
        echo '<div class="wrap aicb-admin"><div id="aicb-admin-root"></div></div>';
    }

    public function shortcode_widget(): string {
        $this->enqueue_widget_assets_now();
        ob_start();
        $this->render_widget_shell('inline');
        return (string) ob_get_clean();
    }

    public function render_footer_widget(): void {
        if (!$this->setting_bool('widget_enabled', true)) {
            return;
        }
        $this->render_widget_shell('floating');
    }

    private function widget_shell_markup(string $mode): string {
        ob_start();
        $this->render_widget_shell($mode);
        return (string) ob_get_clean();
    }

    private function render_widget_shell(string $mode): void {
        $config = $this->public_widget_config();
        $copy = $config['copy'];
        $pack = $this->lang_pack((string) ($config['lang'] ?? 'en'));
        $style = $this->widget_css_vars($config);
        $classes = 'aicb-widget-shell aicb-mode-' . sanitize_html_class($mode);
        $icon_html = $this->icon_html((string) ($copy['icon'] ?? ''));
        $privacy_url = (string) ($config['contact']['privacy_url'] ?? '');
        $inline = $mode === 'inline';
        $dir = !empty($config['rtl']) ? 'rtl' : 'ltr';
        ?>
        <div class="<?php echo esc_attr($classes); ?>" style="<?php echo esc_attr($style); ?>" dir="<?php echo esc_attr($dir); ?>" lang="<?php echo esc_attr((string) ($config['lang'] ?? 'en')); ?>" data-aicb-widget>
            <?php if (!$inline) : ?>
            <div class="aicb-teaser" role="button" tabindex="0" data-aicb-teaser aria-label="<?php echo esc_attr($pack['aria_open']); ?>">
                <span data-aicb-teaser-text></span>
                <button class="aicb-teaser-close" type="button" data-aicb-teaser-close aria-label="<?php echo esc_attr($pack['aria_teaser_close']); ?>">&times;</button>
            </div>
            <button class="aicb-launcher" type="button" aria-label="<?php echo esc_attr($copy['title']); ?>" data-aicb-launcher>
                <span class="aicb-launcher-icon"><?php echo $icon_html ?: $this->default_launcher_icon(); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped ?></span>
            </button>
            <?php endif; ?>
            <div class="aicb-panel" data-aicb-panel <?php echo $inline ? '' : 'hidden'; ?>>
                <div class="aicb-header">
                    <div class="aicb-avatar" data-aicb-avatar aria-hidden="true"><?php echo $icon_html; // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped ?></div>
                    <div class="aicb-meta">
                        <p class="aicb-title"><?php echo esc_html($copy['title']); ?></p>
                        <?php if (trim((string) $copy['status']) !== '') : ?>
                        <p class="aicb-status"><span class="aicb-status-dot"></span><?php echo esc_html($copy['status']); ?></p>
                        <?php endif; ?>
                    </div>
                    <?php if (!$inline) : ?>
                    <div class="aicb-header-actions">
                        <button class="aicb-icon-btn" type="button" data-aicb-minimize aria-label="<?php echo esc_attr($pack['aria_minimize']); ?>">
                            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 12h12"></path></svg>
                        </button>
                        <button class="aicb-icon-btn" type="button" data-aicb-close aria-label="<?php echo esc_attr($pack['aria_close']); ?>">
                            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12"></path><path d="M18 6l-12 12"></path></svg>
                        </button>
                    </div>
                    <?php endif; ?>
                </div>
                <div class="aicb-messages" data-aicb-messages>
                    <div class="aicb-row aicb-bot" data-aicb-intro>
                        <div class="aicb-row-avatar" data-aicb-avatar aria-hidden="true"><?php echo $icon_html; // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped ?></div>
                        <div class="aicb-stack">
                            <div class="aicb-bubble" dir="auto"><?php echo esc_html($copy['intro']); ?></div>
                            <div class="aicb-topics aicb-hidden" data-aicb-topics>
                                <div class="aicb-topics-label" data-aicb-topics-label></div>
                                <div class="aicb-topics-list" data-aicb-topics-list></div>
                            </div>
                        </div>
                    </div>
                    <div class="aicb-list" data-aicb-list></div>
                    <div data-aicb-spacer aria-hidden="true"></div>
                </div>
                <div class="aicb-composer-area">
                    <form class="aicb-composer" data-aicb-form>
                        <textarea data-aicb-input rows="1" autocomplete="off" placeholder="<?php echo esc_attr($copy['placeholder']); ?>"></textarea>
                        <button class="aicb-send aicb-idle" type="submit" data-aicb-send aria-label="<?php echo esc_attr($pack['aria_send']); ?>">
                            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5"></path><path d="M5 12l7-7 7 7"></path></svg>
                        </button>
                    </form>
                    <div class="aicb-footer">
                        <p class="aicb-disclaimer"><?php echo esc_html($copy['disclaimer']); ?></p>
                        <?php if ($privacy_url !== '') : ?>
                        <a class="aicb-privacy" href="<?php echo esc_url($privacy_url); ?>" target="_blank" rel="noreferrer noopener"><?php echo esc_html($copy['privacy_label']); ?></a>
                        <?php endif; ?>
                    </div>
                </div>
            </div>
        </div>
        <?php
    }

    /**
     * Icon des Betreibers: Emoji/Text oder ein eigenes SVG (auf sichere Tags reduziert).
     */
    private function icon_html(string $icon): string {
        $icon = trim($icon);
        if ($icon === '') {
            return '';
        }
        if (stripos($icon, '<svg') === 0) {
            return wp_kses($icon, $this->svg_allowed_tags());
        }
        return esc_html($icon);
    }

    /** Erlaubte SVG-Tags fuer eigene Logos - genutzt beim Speichern und beim Ausgeben. */
    private function svg_allowed_tags(): array {
        $attrs = [
            'fill' => true, 'stroke' => true, 'stroke-width' => true, 'stroke-linecap' => true,
            'stroke-linejoin' => true, 'stroke-miterlimit' => true, 'transform' => true,
            'opacity' => true, 'fill-rule' => true, 'clip-rule' => true,
        ];
        return [
            'svg' => array_merge($attrs, ['xmlns' => true, 'viewbox' => true, 'viewBox' => true, 'width' => true, 'height' => true, 'preserveaspectratio' => true]),
            'g' => $attrs,
            'path' => array_merge($attrs, ['d' => true]),
            'rect' => array_merge($attrs, ['x' => true, 'y' => true, 'width' => true, 'height' => true, 'rx' => true, 'ry' => true]),
            'circle' => array_merge($attrs, ['cx' => true, 'cy' => true, 'r' => true]),
            'ellipse' => array_merge($attrs, ['cx' => true, 'cy' => true, 'rx' => true, 'ry' => true]),
            'line' => array_merge($attrs, ['x1' => true, 'y1' => true, 'x2' => true, 'y2' => true]),
            'polyline' => array_merge($attrs, ['points' => true]),
            'polygon' => array_merge($attrs, ['points' => true]),
        ];
    }

    /**
     * Icon speichern: sanitize_text_field wuerde ein SVG restlos entfernen,
     * deshalb laufen SVGs ueber die Tag-Freigabe und nur Text ueber die
     * Standard-Bereinigung.
     */
    private function sanitize_icon(string $icon): string {
        $trimmed = trim($icon);
        if ($trimmed === '') {
            return '';
        }
        if (stripos($trimmed, '<svg') === 0) {
            return wp_kses($trimmed, $this->svg_allowed_tags());
        }
        return sanitize_text_field($trimmed);
    }

    private function default_launcher_icon(): string {
        return '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
            . '<path d="M6 5h12a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3h-6l-5 3v-3H6a3 3 0 0 1-3-3V8a3 3 0 0 1 3-3z"></path></svg>';
    }

    private function widget_css_vars(array $config): string {
        $theme = $config['theme'] ?? [];
        $vars = [];
        foreach ($theme as $key => $value) {
            $css_key = strtolower(preg_replace('/([a-z])([A-Z])/', '$1-$2', (string) $key));
            $vars[] = '--aicb-' . sanitize_key($css_key) . ':' . sanitize_hex_color($value);
        }
        return implode(';', array_filter($vars));
    }

    public function register_rest_routes(): void {
        register_rest_route(self::REST_NS, '/config', [
            'methods' => 'GET',
            'callback' => fn() => rest_ensure_response($this->public_widget_config()),
            'permission_callback' => '__return_true',
        ]);

        register_rest_route(self::REST_NS, '/session', [
            'methods' => 'POST',
            'callback' => [$this, 'rest_create_session'],
            'permission_callback' => '__return_true',
        ]);

        register_rest_route(self::REST_NS, '/chat', [
            'methods' => 'POST',
            'callback' => [$this, 'rest_chat'],
            'permission_callback' => '__return_true',
        ]);

        register_rest_route(self::REST_NS, '/feedback', [
            'methods' => 'POST',
            'callback' => [$this, 'rest_feedback'],
            'permission_callback' => '__return_true',
        ]);

        $admin_routes = [
            ['/admin/settings', ['GET', 'POST'], 'rest_admin_settings'],
            ['/admin/widget', ['GET', 'POST'], 'rest_admin_widget'],
            ['/admin/faqs', ['GET', 'POST'], 'rest_admin_faqs'],
            ['/admin/train/start', ['POST'], 'rest_train_start'],
            ['/admin/train/step', ['POST'], 'rest_train_step'],
            ['/admin/train/status', ['GET'], 'rest_train_status'],
            ['/admin/memory', ['GET', 'POST', 'DELETE'], 'rest_admin_memory'],
            ['/admin/stats', ['GET'], 'rest_admin_stats'],
            ['/admin/post-types', ['GET'], 'rest_post_types'],
            ['/admin/content', ['GET', 'POST'], 'rest_admin_content'],
        ];

        foreach ($admin_routes as [$route, $methods, $callback]) {
            register_rest_route(self::REST_NS, $route, [
                'methods' => $methods,
                'callback' => [$this, $callback],
                'permission_callback' => fn() => current_user_can('manage_options'),
            ]);
        }
    }

    public function rest_create_session(WP_REST_Request $request): WP_REST_Response {
        return rest_ensure_response($this->create_session_payload());
    }

    public function rest_chat(WP_REST_Request $request): WP_REST_Response {
        $params = $request->get_json_params();
        $question = sanitize_textarea_field((string) ($params['question'] ?? $params['message'] ?? ''));
        $history = is_array($params['history'] ?? null) ? $params['history'] : [];
        $lang = sanitize_key((string) ($params['lang'] ?? 'de'));
        // Bereits gezeigte Buttons: verhindert dieselbe Empfehlung zweimal.
        $offered = [];
        foreach ((array) ($params['offered'] ?? []) as $item) {
            $label = sanitize_text_field((string) $item);
            if ($label !== '') {
                $offered[] = $label;
            }
        }
        $offered = array_slice($offered, -10);

        if ($question === '') {
            return new WP_REST_Response(['error' => 'Keine Frage uebergeben.'], 400);
        }

        $session_payload = $this->ensure_session_payload((string) ($params['session_token'] ?? ''));
        $session_hash = $session_payload['session_hash'];

        try {
            $answer_payload = $this->answer_question($question, $history, $lang, $offered);
            $event_id = $this->record_event($session_hash, $question, $answer_payload['answer'], 'ok', null, $answer_payload['usage']);
            $this->touch_session($session_hash);
            return rest_ensure_response([
                'answer' => $answer_payload['answer'],
                'sources' => $answer_payload['sources'],
                'rich' => $answer_payload['rich'],
                'event_id' => $event_id,
                'session_token' => $session_payload['token'],
                'session_expires_at' => $session_payload['expires_at'],
            ]);
        } catch (Throwable $e) {
            $this->record_event($session_hash, $question, null, 'error', $e->getMessage(), []);
            return new WP_REST_Response([
                'error' => $e->getMessage(),
                'session_token' => $session_payload['token'],
                'session_expires_at' => $session_payload['expires_at'],
            ], 500);
        }
    }

    /**
     * Nimmt die 👍/👎-Bewertung zu einer Antwort entgegen. Nur der Besitzer der
     * Session (passender session_token) darf sein eigenes Event bewerten.
     */
    public function rest_feedback(WP_REST_Request $request): WP_REST_Response {
        global $wpdb;
        $params = $request->get_json_params();
        $event_id = (int) ($params['event_id'] ?? 0);
        $raw_value = (int) ($params['value'] ?? 0);
        $token = trim((string) ($params['session_token'] ?? ''));

        if ($event_id <= 0 || $token === '') {
            return new WP_REST_Response(['error' => 'event_id und session_token erforderlich.'], 400);
        }
        // 1 = hilfreich, -1 = nicht hilfreich, 0 = zuruecknehmen.
        $value = $raw_value > 0 ? 1 : ($raw_value < 0 ? -1 : 0);
        $hash = $this->hash_token($token);

        $events = $wpdb->prefix . 'aicb_events';
        $owner = $wpdb->get_var($wpdb->prepare(
            "SELECT session_hash FROM {$events} WHERE id = %d",
            $event_id
        ));
        if ($owner === null) {
            return new WP_REST_Response(['error' => 'Antwort nicht gefunden.'], 404);
        }
        if (!hash_equals((string) $owner, $hash)) {
            return new WP_REST_Response(['error' => 'Keine Berechtigung fuer diese Antwort.'], 403);
        }

        $wpdb->update($events, ['feedback' => $value], ['id' => $event_id], ['%d'], ['%d']);
        return rest_ensure_response(['status' => 'ok', 'value' => $value]);
    }

    public function rest_admin_settings(WP_REST_Request $request): WP_REST_Response {
        if ($request->get_method() === 'GET') {
            return rest_ensure_response($this->settings_for_admin());
        }

        $payload = $request->get_json_params();
        $settings = $this->settings();
        $allowed = [
            'chat_model', 'embedding_model', 'retriever_k', 'max_context_chars', 'batch_size',
            'auto_index_on_save', 'widget_enabled', 'enabled_post_types', 'include_excerpts',
            'include_taxonomies', 'privacy_url', 'contact_url', 'contact_email', 'contact_phone',
            'system_prompt',
        ];
        foreach ($allowed as $key) {
            if (!array_key_exists($key, $payload)) {
                continue;
            }
            $settings[$key] = $this->sanitize_setting_value($key, $payload[$key]);
        }
        if (array_key_exists('openai_api_key', $payload)) {
            $new_key = trim((string) $payload['openai_api_key']);
            if ($new_key !== '' && $new_key !== '********') {
                $settings['openai_api_key'] = $new_key;
            }
        }

        update_option(self::OPTION_KEY, $settings, false);
        return rest_ensure_response($this->settings_for_admin());
    }

    public function rest_admin_widget(WP_REST_Request $request): WP_REST_Response {
        if ($request->get_method() === 'GET') {
            return rest_ensure_response(get_option(self::WIDGET_OPTION_KEY, self::default_widget_config()));
        }
        $payload = $request->get_json_params();
        $config = $this->sanitize_widget_config(is_array($payload) ? $payload : []);
        update_option(self::WIDGET_OPTION_KEY, $config, false);
        return rest_ensure_response($config);
    }

    public function rest_admin_faqs(WP_REST_Request $request): WP_REST_Response {
        if ($request->get_method() === 'GET') {
            return rest_ensure_response(['faqs' => $this->faqs()]);
        }
        $payload = $request->get_json_params();
        $faqs = [];
        foreach ((array) ($payload['faqs'] ?? []) as $item) {
            $q = sanitize_textarea_field((string) ($item['question'] ?? ''));
            $a = sanitize_textarea_field((string) ($item['answer'] ?? ''));
            if ($q !== '' || $a !== '') {
                $faqs[] = ['question' => $q, 'answer' => $a];
            }
        }
        update_option(self::FAQ_OPTION_KEY, $faqs, false);
        return rest_ensure_response(['status' => 'ok', 'faqs' => $faqs]);
    }

    public function rest_post_types(): WP_REST_Response {
        return rest_ensure_response(['post_types' => $this->available_post_types()]);
    }

    /**
     * Inhalte-Tab: Liste veroeffentlichter Inhalte (mit Auswahlstatus) + gewaehlte PDFs.
     * GET  -> aktueller Stand. POST -> Auswahl speichern und aktualisierten Stand liefern.
     * Es werden ausschliesslich veroeffentlichte Inhalte gelistet (keine Entwuerfe).
     */
    public function rest_admin_content(WP_REST_Request $request): WP_REST_Response {
        if ($request->get_method() === 'POST') {
            $payload = $request->get_json_params();
            $mode = (($payload['mode'] ?? 'all') === 'selected') ? 'selected' : 'all';
            $post_ids = array_values(array_unique(array_filter(array_map('intval', (array) ($payload['post_ids'] ?? [])))));
            $pdf_ids = array_values(array_unique(array_filter(array_map('intval', (array) ($payload['pdf_ids'] ?? [])))));

            update_option(self::INDEX_MODE_OPTION, $mode, false);
            update_option(self::SELECTED_POSTS_OPTION, $post_ids, false);
            update_option(self::SELECTED_PDFS_OPTION, $pdf_ids, false);
        }

        $search = trim((string) $request->get_param('q'));
        return rest_ensure_response($this->content_overview($search));
    }

    private function content_overview(string $search = ''): array {
        $selected_posts = array_flip(array_map('intval', (array) get_option(self::SELECTED_POSTS_OPTION, [])));
        $per_type_cap = 300;

        $groups = [];
        foreach ($this->available_post_types_without_selection() as $type) {
            $args = [
                'post_type' => $type['name'],
                'post_status' => 'publish',
                'posts_per_page' => $per_type_cap,
                'orderby' => 'title',
                'order' => 'ASC',
                'no_found_rows' => false,
                'ignore_sticky_posts' => true,
                'suppress_filters' => false,
            ];
            if ($search !== '') {
                $args['s'] = $search;
            }
            $query = new WP_Query($args);
            $items = [];
            foreach ($query->posts as $post) {
                $items[] = [
                    'id' => (int) $post->ID,
                    'title' => html_entity_decode(get_the_title($post) ?: ('#' . $post->ID), ENT_QUOTES),
                    'url' => get_permalink($post),
                    'selected' => isset($selected_posts[(int) $post->ID]),
                ];
            }
            $total = (int) $query->found_posts;
            wp_reset_postdata();

            if (!$items) {
                continue;
            }
            $groups[] = [
                'name' => $type['name'],
                'label' => $type['label'],
                'total' => $total,
                'truncated' => $total > count($items),
                'items' => $items,
            ];
        }

        $pdfs = [];
        foreach ($this->selected_pdf_ids() as $aid) {
            $pdfs[] = [
                'id' => (int) $aid,
                'title' => html_entity_decode(get_the_title($aid) ?: wp_basename((string) get_attached_file($aid)), ENT_QUOTES),
                'url' => wp_get_attachment_url($aid),
            ];
        }

        return [
            'mode' => $this->index_mode(),
            'post_types' => $groups,
            'pdfs' => $pdfs,
            'selected_count' => count($selected_posts),
        ];
    }

    public function rest_train_start(WP_REST_Request $request): WP_REST_Response {
        $params = $request->get_json_params();
        $clear = !array_key_exists('clear', $params) || rest_sanitize_boolean($params['clear']);
        $job = $this->create_training_job($clear);
        return rest_ensure_response($job);
    }

    public function rest_train_step(WP_REST_Request $request): WP_REST_Response {
        $params = $request->get_json_params();
        $job_id = sanitize_key((string) ($params['job_id'] ?? ''));
        if ($job_id === '') {
            return new WP_REST_Response(['error' => 'job_id fehlt.'], 400);
        }
        try {
            return rest_ensure_response($this->run_training_step($job_id));
        } catch (Throwable $e) {
            return new WP_REST_Response(['error' => $e->getMessage()], 500);
        }
    }

    public function rest_train_status(WP_REST_Request $request): WP_REST_Response {
        $job_id = sanitize_key((string) $request->get_param('job_id'));
        $job = $job_id ? get_transient($this->job_key($job_id)) : get_option('aicb_last_training_job');
        return rest_ensure_response($job ?: ['status' => 'idle']);
    }

    public function rest_admin_memory(WP_REST_Request $request): WP_REST_Response {
        global $wpdb;
        $table = $wpdb->prefix . 'aicb_chunks';

        if ($request->get_method() === 'GET') {
            $q = trim((string) $request->get_param('q'));
            $limit = max(1, min(200, (int) ($request->get_param('limit') ?: 80)));
            if ($q !== '') {
                $like = '%' . $wpdb->esc_like($q) . '%';
                $rows = $wpdb->get_results($wpdb->prepare(
                    "SELECT id, source_id, source_type, source_url, title, section, content, token_estimate, updated_at
                     FROM {$table}
                     WHERE title LIKE %s OR content LIKE %s OR source_url LIKE %s
                     ORDER BY updated_at DESC LIMIT %d",
                    $like,
                    $like,
                    $like,
                    $limit
                ), ARRAY_A);
            } else {
                $rows = $wpdb->get_results($wpdb->prepare(
                    "SELECT id, source_id, source_type, source_url, title, section, content, token_estimate, updated_at
                     FROM {$table}
                     ORDER BY updated_at DESC LIMIT %d",
                    $limit
                ), ARRAY_A);
            }
            return rest_ensure_response(['items' => $rows ?: [], 'total' => (int) $wpdb->get_var("SELECT COUNT(*) FROM {$table}")]);
        }

        $payload = $request->get_json_params();
        $id = absint($payload['id'] ?? 0);
        if (!$id) {
            return new WP_REST_Response(['error' => 'id fehlt.'], 400);
        }

        if ($request->get_method() === 'DELETE') {
            $wpdb->delete($table, ['id' => $id], ['%d']);
            return rest_ensure_response(['status' => 'ok', 'deleted' => $id]);
        }

        $content = sanitize_textarea_field((string) ($payload['content'] ?? ''));
        $title = sanitize_text_field((string) ($payload['title'] ?? ''));
        if ($content === '') {
            return new WP_REST_Response(['error' => 'content fehlt.'], 400);
        }
        $embedding = $this->embed_text($content);
        $wpdb->update($table, [
            'title' => $title,
            'content' => $content,
            'content_hash' => sha1($content),
            'embedding' => wp_json_encode($embedding),
            'token_estimate' => $this->estimate_tokens($content),
            'updated_at' => gmdate('Y-m-d H:i:s'),
        ], ['id' => $id], ['%s', '%s', '%s', '%s', '%d', '%s'], ['%d']);
        return rest_ensure_response(['status' => 'ok', 'id' => $id]);
    }

    public function rest_admin_stats(): WP_REST_Response {
        global $wpdb;
        $events = $wpdb->prefix . 'aicb_events';
        $chunks = $wpdb->prefix . 'aicb_chunks';
        $since_week = gmdate('Y-m-d H:i:s', time() - 7 * DAY_IN_SECONDS);
        $since_month = gmdate('Y-m-d H:i:s', time() - 30 * DAY_IN_SECONDS);

        $rows = $wpdb->get_results("SELECT question, answer, status, input_tokens, output_tokens, created_at FROM {$events} ORDER BY created_at DESC LIMIT 1000", ARRAY_A);
        $top = [];
        $daily = [];
        $input = 0;
        $output = 0;
        $answered = 0;
        foreach ($rows ?: [] as $row) {
            $q = trim((string) $row['question']);
            if ($q !== '') {
                $top[$q] = ($top[$q] ?? 0) + 1;
            }
            $day = gmdate('Y-m-d', strtotime((string) $row['created_at']));
            $daily[$day] = ($daily[$day] ?? 0) + 1;
            $input += (int) $row['input_tokens'];
            $output += (int) $row['output_tokens'];
            if (in_array(strtolower((string) $row['status']), ['ok', 'answered', 'success'], true)) {
                $answered++;
            }
        }
        arsort($top);

        // Feedback (👍/👎) auswerten.
        $helpful = (int) $wpdb->get_var("SELECT COUNT(*) FROM {$events} WHERE feedback = 1");
        $not_helpful = (int) $wpdb->get_var("SELECT COUNT(*) FROM {$events} WHERE feedback = -1");
        $rated = $helpful + $not_helpful;
        $helpful_month = (int) $wpdb->get_var($wpdb->prepare("SELECT COUNT(*) FROM {$events} WHERE feedback = 1 AND created_at >= %s", $since_month));
        $not_helpful_month = (int) $wpdb->get_var($wpdb->prepare("SELECT COUNT(*) FROM {$events} WHERE feedback = -1 AND created_at >= %s", $since_month));
        $feedback = [
            'helpful' => $helpful,
            'not_helpful' => $not_helpful,
            'rated' => $rated,
            'satisfaction' => $rated > 0 ? (int) round(100 * $helpful / $rated) : null,
            'helpful_month' => $helpful_month,
            'not_helpful_month' => $not_helpful_month,
        ];

        return rest_ensure_response([
            'overview' => [
                'week_chats' => (int) $wpdb->get_var($wpdb->prepare("SELECT COUNT(*) FROM {$events} WHERE created_at >= %s", $since_week)),
                'month_chats' => (int) $wpdb->get_var($wpdb->prepare("SELECT COUNT(*) FROM {$events} WHERE created_at >= %s", $since_month)),
                'total_chats' => (int) $wpdb->get_var("SELECT COUNT(*) FROM {$events}"),
                'answered' => $answered,
                'chunks' => (int) $wpdb->get_var("SELECT COUNT(*) FROM {$chunks}"),
            ],
            'feedback' => $feedback,
            'top_questions' => array_slice(array_map(fn($q, $c) => ['question' => $q, 'count' => $c], array_keys($top), $top), 0, 10),
            'daily' => $daily,
            'usage' => [
                'input_tokens' => $input,
                'output_tokens' => $output,
                'estimated_cost_usd' => $this->estimate_cost($input, $output),
                'model' => $this->setting('chat_model', 'gpt-4o-mini'),
            ],
        ]);
    }

    public function schedule_post_reindex(int $post_id, WP_Post $post, bool $update): void {
        if (!$this->setting_bool('auto_index_on_save', true)) {
            return;
        }
        if (wp_is_post_revision($post_id) || wp_is_post_autosave($post_id)) {
            return;
        }
        if ($post->post_status !== 'publish') {
            return;
        }
        // Im Modus "Nur ausgewaehlte" nur nachindexieren, wenn der Post ausgewaehlt ist;
        // im Modus "Alle" gilt weiterhin die Post-Type-Auswahl aus den Einstellungen.
        if ($this->index_mode() === 'selected') {
            if (!in_array($post_id, $this->selected_post_ids_raw(), true)) {
                return;
            }
        } elseif (!in_array($post->post_type, $this->enabled_post_type_names(), true)) {
            return;
        }
        if (!wp_next_scheduled('aicb_reindex_single_post', [$post_id])) {
            wp_schedule_single_event(time() + 60, 'aicb_reindex_single_post', [$post_id]);
        }
    }

    public function cron_reindex_single_post(int $post_id): void {
        $post = get_post($post_id);
        if (!$post || $post->post_status !== 'publish') {
            $this->delete_source_chunks('post:' . $post_id);
            return;
        }
        // Abgewaehlte Inhalte im Selektiv-Modus nicht (wieder) aufnehmen.
        if ($this->index_mode() === 'selected' && !in_array($post_id, $this->selected_post_ids_raw(), true)) {
            $this->delete_source_chunks('post:' . $post_id);
            return;
        }
        try {
            $this->index_post($post);
        } catch (Throwable $e) {
            error_log('AICB post reindex failed: ' . $e->getMessage());
        }
    }

    private function create_training_job(bool $clear): array {
        if ($clear) {
            $this->clear_chunks();
        }

        // Queue aus ausgewaehlten (bzw. allen veroeffentlichten) Posts + gewaehlten PDFs.
        $queue = [];
        foreach ($this->training_post_ids() as $post_id) {
            $queue[] = ['kind' => 'post', 'id' => (int) $post_id];
        }
        foreach ($this->selected_pdf_ids() as $pdf_id) {
            $queue[] = ['kind' => 'pdf', 'id' => (int) $pdf_id];
        }

        $mode_label = $this->index_mode() === 'selected' ? 'Nur ausgewaehlte Inhalte' : 'Alle veroeffentlichten Inhalte';
        $job = [
            'job_id' => wp_generate_uuid4(),
            'status' => 'running',
            'queue' => $queue,
            'total' => count($queue),
            'cursor' => 0,
            'processed' => 0,
            'chunks' => 0,
            'logs' => [sprintf('Training gestartet (%s, %d Quellen).', $mode_label, count($queue))],
            'faq_indexed' => false,
            'started_at' => gmdate('c'),
            'finished_at' => null,
        ];
        set_transient($this->job_key($job['job_id']), $job, DAY_IN_SECONDS);
        update_option('aicb_last_training_job', $this->public_job($job), false);
        return $this->public_job($job);
    }

    private function run_training_step(string $job_id): array {
        $job = get_transient($this->job_key($job_id));
        if (!$job || !is_array($job)) {
            throw new RuntimeException('Training-Job nicht gefunden oder abgelaufen.');
        }
        if (($job['status'] ?? '') === 'done') {
            return $this->public_job($job);
        }

        $batch = max(1, min(20, (int) $this->setting('batch_size', 4)));
        $queue = $job['queue'] ?? [];
        $cursor = (int) ($job['cursor'] ?? 0);
        $slice = array_slice($queue, $cursor, $batch);

        foreach ($slice as $item) {
            $kind = (string) ($item['kind'] ?? 'post');
            $id = (int) ($item['id'] ?? 0);
            $job['cursor']++;

            if ($kind === 'pdf') {
                try {
                    $count = $this->index_pdf($id);
                    $job['chunks'] += $count;
                    $job['processed']++;
                    if ($count > 0) {
                        $job['logs'][] = sprintf('PDF indexiert: #%d %s (%d Chunks)', $id, get_the_title($id), $count);
                    } else {
                        $job['logs'][] = sprintf('PDF ohne Textebene uebersprungen: #%d %s', $id, get_the_title($id));
                    }
                } catch (Throwable $e) {
                    $job['logs'][] = sprintf('PDF-Fehler #%d: %s', $id, $e->getMessage());
                }
                continue;
            }

            $post = get_post($id);
            if (!$post || $post->post_status !== 'publish') {
                $job['logs'][] = "Uebersprungen: Post {$id}";
                continue;
            }
            $count = $this->index_post($post);
            $job['chunks'] += $count;
            $job['processed']++;
            $job['logs'][] = sprintf('Indexiert: #%d %s (%d Chunks)', $post->ID, get_the_title($post), $count);
        }

        if ($job['cursor'] >= $job['total'] && empty($job['faq_indexed'])) {
            $faq_chunks = $this->index_faqs();
            $job['chunks'] += $faq_chunks;
            $job['faq_indexed'] = true;
            $job['logs'][] = sprintf('FAQs indexiert (%d Chunks)', $faq_chunks);
        }

        if ($job['cursor'] >= $job['total'] && !empty($job['faq_indexed'])) {
            $job['status'] = 'done';
            $job['finished_at'] = gmdate('c');
            $job['logs'][] = 'Training abgeschlossen.';
        }

        $job['logs'] = array_slice($job['logs'], -80);
        set_transient($this->job_key($job_id), $job, DAY_IN_SECONDS);
        update_option('aicb_last_training_job', $this->public_job($job), false);
        return $this->public_job($job);
    }

    private function index_post(WP_Post $post): int {
        $source_id = 'post:' . $post->ID;
        $this->delete_source_chunks($source_id);
        $document = $this->post_to_document($post);
        if (trim($document['content']) === '') {
            return 0;
        }
        return $this->insert_document_chunks($source_id, $post->post_type, $document['url'], $document['title'], $document['content']);
    }

    private function index_faqs(): int {
        $this->delete_source_chunks_by_type('faq');
        $count = 0;
        foreach ($this->faqs() as $idx => $faq) {
            $title = trim((string) ($faq['question'] ?? 'FAQ'));
            $body = "Frage: {$title}\n\nAntwort: " . trim((string) ($faq['answer'] ?? ''));
            if (trim($body) === '') {
                continue;
            }
            $count += $this->insert_document_chunks('faq:' . $idx, 'faq', home_url('/'), $title, $body);
        }
        return $count;
    }

    /* ----------------------------------------------------------------------
     * Feingranulare Auswahl (Inhalte-Tab)
     * -------------------------------------------------------------------- */

    private function index_mode(): string {
        return get_option(self::INDEX_MODE_OPTION, 'all') === 'selected' ? 'selected' : 'all';
    }

    private function selected_post_ids_raw(): array {
        return array_values(array_filter(array_map('intval', (array) get_option(self::SELECTED_POSTS_OPTION, []))));
    }

    /** Post-IDs fuer das Training: nur ausgewaehlte (Selektiv) bzw. alle veroeffentlichten (Alle). */
    private function training_post_ids(): array {
        if ($this->index_mode() === 'selected') {
            $ids = [];
            foreach ($this->selected_post_ids_raw() as $id) {
                $post = get_post($id);
                if ($post && $post->post_status === 'publish') {
                    $ids[] = (int) $id;
                }
            }
            return $ids;
        }

        $query = new WP_Query([
            'post_type' => $this->enabled_post_type_names(),
            'post_status' => 'publish',
            'posts_per_page' => -1,
            'fields' => 'ids',
            'orderby' => 'ID',
            'order' => 'ASC',
            'no_found_rows' => true,
        ]);
        return array_map('intval', $query->posts ?: []);
    }

    /** Gueltige, ausgewaehlte PDF-Attachments. */
    private function selected_pdf_ids(): array {
        $ids = [];
        foreach (array_map('intval', (array) get_option(self::SELECTED_PDFS_OPTION, [])) as $id) {
            if ($id <= 0) {
                continue;
            }
            $post = get_post($id);
            if ($post && $post->post_type === 'attachment' && get_post_mime_type($id) === 'application/pdf') {
                $ids[] = $id;
            }
        }
        return array_values(array_unique($ids));
    }

    /* ----------------------------------------------------------------------
     * PDF-Indexierung (Mediathek)
     * -------------------------------------------------------------------- */

    private function index_pdf(int $attachment_id): int {
        $source_id = 'pdf:' . $attachment_id;
        $this->delete_source_chunks($source_id);

        if (get_post_mime_type($attachment_id) !== 'application/pdf') {
            return 0;
        }
        $path = get_attached_file($attachment_id);
        if (!$path || !file_exists($path) || !is_readable($path)) {
            throw new RuntimeException('PDF-Datei nicht gefunden.');
        }
        // 1) Beste & robusteste Extraktion: gebuendelte Bibliothek (Smalot/PdfParser).
        //    Beherrscht CID/Type0, ToUnicode, Differences, CFF und Positionierung.
        $text = $this->normalize_text($this->pdf_text_via_smalot($path));

        // 2) Fallback: pdftotext (poppler), falls auf dem Host verfuegbar.
        if (trim($text) === '' || !$this->pdf_looks_like_text($text)) {
            $text = $this->normalize_text($this->pdf_text_via_pdftotext($path));
        }

        // 3) Letzter Fallback: eingebaute reine PHP-Extraktion.
        if (trim($text) === '' || !$this->pdf_looks_like_text($text)) {
            $bytes = (string) file_get_contents($path);
            if ($bytes !== '') {
                $text = $this->normalize_text($this->pdf_to_text($bytes));
            }
        }

        if (trim($text) === '' || !$this->pdf_looks_like_text($text)) {
            // Kein lesbarer Textlayer: gescanntes Bild-PDF, verschluesselt oder
            // Font ohne verwertbare Kodierung - nichts indexieren.
            return 0;
        }

        $title = trim((string) get_the_title($attachment_id));
        if ($title === '') {
            $title = wp_basename($path);
        }
        $url = wp_get_attachment_url($attachment_id) ?: home_url('/');
        $content = '# ' . $title . "\n\n" . $text . "\n\nQuelle: " . $url;
        return $this->insert_document_chunks($source_id, 'pdf', $url, $title, $content);
    }

    /**
     * Extrahiert Text aus einem PDF (reines PHP, ohne externe Bibliothek).
     * Unterstuetzt FlateDecode-Streams, literale/hexadezimale Strings, Tj/TJ
     * sowie ToUnicode-CMaps (bfchar/bfrange). Gescannte (nur Bild-) PDFs und
     * verschluesselte PDFs liefern keinen Text.
     */
    private function pdf_to_text(string $bytes): string {
        $streams = $this->pdf_decode_streams($bytes);
        if (!$streams) {
            return '';
        }
        $cmap = $this->pdf_build_tounicode($streams);
        // Fonts mit /Encoding /Differences (Glyphnamen) statt ToUnicode: haeufig
        // bei Subset-Fonts aus Word/LibreOffice/InDesign. Ohne diese Abbildung
        // waere der Text Zeichensalat.
        $diff = $this->pdf_build_differences_map($streams, $bytes);
        $parts = [];
        foreach ($streams as $stream) {
            if (strpos($stream, 'Tj') === false && strpos($stream, 'TJ') === false) {
                continue;
            }
            $parts[] = $this->pdf_tokenize_text($stream, $cmap['map'], (int) $cmap['code_len'], $diff);
        }
        return trim(implode("\n", array_filter($parts)));
    }

    /**
     * Extrahiert Text mit der gebuendelten Bibliothek Smalot/PdfParser (reines PHP).
     * Wird lazy geladen und nur beim Indexieren eines PDFs benoetigt.
     */
    private function pdf_text_via_smalot(string $path): string {
        if (!class_exists('\\Smalot\\PdfParser\\Parser')) {
            $autoload = plugin_dir_path(__FILE__) . 'vendor/autoload.php';
            if (!is_readable($autoload)) {
                return '';
            }
            require_once $autoload;
            if (!class_exists('\\Smalot\\PdfParser\\Parser')) {
                return '';
            }
        }
        try {
            // Bilder nicht im Speicher halten - spart RAM bei bildlastigen PDFs.
            if (class_exists('\\Smalot\\PdfParser\\Config')) {
                $config = new \Smalot\PdfParser\Config();
                if (method_exists($config, 'setRetainImageContent')) {
                    $config->setRetainImageContent(false);
                }
                $parser = new \Smalot\PdfParser\Parser([], $config);
            } else {
                $parser = new \Smalot\PdfParser\Parser();
            }
            $pdf = $parser->parseFile($path);
            return (string) $pdf->getText();
        } catch (\Throwable $e) {
            error_log('AICB Smalot PDF-Parsing fehlgeschlagen (' . wp_basename($path) . '): ' . $e->getMessage());
            return '';
        }
    }

    /** Ruft pdftotext (poppler) auf, falls verfuegbar. Sonst leerer String. */
    private function pdf_text_via_pdftotext(string $path): string {
        if (!function_exists('shell_exec')) {
            return '';
        }
        $disabled = array_map('trim', explode(',', (string) ini_get('disable_functions')));
        if (in_array('shell_exec', $disabled, true)) {
            return '';
        }
        // -q still, -enc UTF-8, Ausgabe nach stdout ("-").
        $out = @shell_exec('pdftotext -q -enc UTF-8 ' . escapeshellarg($path) . ' - 2>/dev/null');
        return is_string($out) ? $out : '';
    }

    /**
     * Baut aus allen /Encoding /Differences-Arrays eine Abbildung Byte->UTF-8.
     * Global gemergt (erste Zuordnung gewinnt) - deckt den haeufigen Fall eines
     * konsistenten Zeichensatzes ab.
     */
    private function pdf_build_differences_map(array $streams, string $bytes): array {
        $diff = [];
        $sources = $streams;
        $sources[] = $bytes; // Font-Dicts liegen oft unkomprimiert vor.
        foreach ($sources as $text) {
            if (strpos($text, '/Differences') === false) {
                continue;
            }
            if (!preg_match_all('/\/Differences\s*\[(.*?)\]/s', $text, $blocks)) {
                continue;
            }
            foreach ($blocks[1] as $arr) {
                if (!preg_match_all('/(\d+)|\/([A-Za-z0-9._]+)/', $arr, $toks, PREG_SET_ORDER)) {
                    continue;
                }
                $code = 0;
                foreach ($toks as $t) {
                    if (($t[1] ?? '') !== '') {
                        $code = (int) $t[1];
                    } else {
                        $cp = $this->pdf_glyph_to_codepoint($t[2]);
                        if ($cp !== null && $code >= 0 && $code <= 255 && !isset($diff[$code])) {
                            $diff[$code] = $this->pdf_codepoint_to_utf8($cp);
                        }
                        $code++;
                    }
                }
            }
        }
        return $diff;
    }

    /** Adobe-Glyphname -> Unicode-Codepoint (Teilmenge + uniXXXX + Einzelzeichen). */
    private function pdf_glyph_to_codepoint(string $name): ?int {
        if ($name === '' || $name === '.notdef') {
            return null;
        }
        static $agl = [
            'space' => 32, 'exclam' => 33, 'quotedbl' => 34, 'numbersign' => 35, 'dollar' => 36,
            'percent' => 37, 'ampersand' => 38, 'quotesingle' => 39, 'parenleft' => 40, 'parenright' => 41,
            'asterisk' => 42, 'plus' => 43, 'comma' => 44, 'hyphen' => 45, 'period' => 46, 'slash' => 47,
            'zero' => 48, 'one' => 49, 'two' => 50, 'three' => 51, 'four' => 52, 'five' => 53, 'six' => 54,
            'seven' => 55, 'eight' => 56, 'nine' => 57, 'colon' => 58, 'semicolon' => 59, 'less' => 60,
            'equal' => 61, 'greater' => 62, 'question' => 63, 'at' => 64, 'bracketleft' => 91,
            'backslash' => 92, 'bracketright' => 93, 'asciicircum' => 94, 'underscore' => 95, 'grave' => 96,
            'braceleft' => 123, 'bar' => 124, 'braceright' => 125, 'asciitilde' => 126,
            'quoteleft' => 0x2018, 'quoteright' => 0x2019, 'quotedblleft' => 0x201C, 'quotedblright' => 0x201D,
            'quotesinglbase' => 0x201A, 'quotedblbase' => 0x201E, 'bullet' => 0x2022, 'endash' => 0x2013,
            'emdash' => 0x2014, 'ellipsis' => 0x2026, 'guillemotleft' => 0xAB, 'guillemotright' => 0xBB,
            'guilsinglleft' => 0x2039, 'guilsinglright' => 0x203A, 'Euro' => 0x20AC, 'trademark' => 0x2122,
            'degree' => 0xB0, 'plusminus' => 0xB1, 'section' => 0xA7, 'paragraph' => 0xB6,
            'periodcentered' => 0xB7, 'cent' => 0xA2, 'sterling' => 0xA3, 'yen' => 0xA5, 'copyright' => 0xA9,
            'registered' => 0xAE, 'ordfeminine' => 0xAA, 'ordmasculine' => 0xBA,
            'germandbls' => 0xDF, 'adieresis' => 0xE4, 'odieresis' => 0xF6, 'udieresis' => 0xFC,
            'Adieresis' => 0xC4, 'Odieresis' => 0xD6, 'Udieresis' => 0xDC,
            'aacute' => 0xE1, 'agrave' => 0xE0, 'acircumflex' => 0xE2, 'atilde' => 0xE3, 'aring' => 0xE5,
            'ae' => 0xE6, 'ccedilla' => 0xE7, 'eacute' => 0xE9, 'egrave' => 0xE8, 'ecircumflex' => 0xEA,
            'edieresis' => 0xEB, 'iacute' => 0xED, 'igrave' => 0xEC, 'icircumflex' => 0xEE, 'idieresis' => 0xEF,
            'ntilde' => 0xF1, 'oacute' => 0xF3, 'ograve' => 0xF2, 'ocircumflex' => 0xF4, 'otilde' => 0xF5,
            'oslash' => 0xF8, 'uacute' => 0xFA, 'ugrave' => 0xF9, 'ucircumflex' => 0xFB, 'yacute' => 0xFD,
            'ydieresis' => 0xFF, 'Aacute' => 0xC1, 'Agrave' => 0xC0, 'Acircumflex' => 0xC2, 'Atilde' => 0xC3,
            'Aring' => 0xC5, 'AE' => 0xC6, 'Ccedilla' => 0xC7, 'Eacute' => 0xC9, 'Egrave' => 0xC8,
            'Ecircumflex' => 0xCA, 'Edieresis' => 0xCB, 'Iacute' => 0xCD, 'Igrave' => 0xCC, 'Ntilde' => 0xD1,
            'Oacute' => 0xD3, 'Ograve' => 0xD2, 'Ocircumflex' => 0xD4, 'Otilde' => 0xD5, 'Oslash' => 0xD8,
            'Uacute' => 0xDA, 'Ugrave' => 0xD9, 'Ucircumflex' => 0xDB, 'Yacute' => 0xDD,
            'fi' => 0xFB01, 'fl' => 0xFB02,
        ];
        if (isset($agl[$name])) {
            return $agl[$name];
        }
        if (strlen($name) === 1) {
            return ord($name); // A-Z, a-z, ASCII-Symbole
        }
        if (preg_match('/^uni([0-9A-Fa-f]{4})$/', $name, $m)) {
            return hexdec($m[1]);
        }
        if (preg_match('/^u([0-9A-Fa-f]{4,6})$/', $name, $m)) {
            return hexdec($m[1]);
        }
        // Namen wie "g12" / "cid34" o. ae. sind ohne Font nicht aufloesbar.
        return null;
    }

    /**
     * Qualitaetsgate: Erkennt, ob der extrahierte Text echter Fliesstext ist.
     * PDFs mit Subset-Fonts ohne ToUnicode liefern falsch gemappte Glyphen
     * (Zeichensalat) - solcher "Text" darf nicht in den Index gelangen.
     */
    private function pdf_looks_like_text(string $text): bool {
        $trim = trim($text);
        if ($trim === '') {
            return false;
        }
        $nonspace = preg_replace('/\s+/u', '', $trim);
        $letters = preg_replace('/[^\p{L}]/u', '', $trim);
        $nonspace_len = function_exists('mb_strlen') ? mb_strlen((string) $nonspace) : strlen((string) $nonspace);
        $letters_len = function_exists('mb_strlen') ? mb_strlen((string) $letters) : strlen((string) $letters);
        $letter_ratio = $nonspace_len > 0 ? $letters_len / $nonspace_len : 0.0;

        // Zu wenige Buchstaben (fast nur Symbole/Zahlen) => kein sinnvoller Text.
        if ($letter_ratio < 0.45) {
            return false;
        }

        // Nicht-lateinische Schriften (kyrillisch, arabisch, CJK, Hangul): die
        // Wortliste greift nicht, ein guter Buchstabenanteil genuegt.
        if (preg_match('/[\x{0400}-\x{04FF}\x{0600}-\x{06FF}\x{4E00}-\x{9FFF}\x{3040}-\x{30FF}\x{AC00}-\x{D7AF}]/u', $trim)) {
            return $letter_ratio >= 0.5;
        }

        $lower = ' ' . (function_exists('mb_strtolower') ? mb_strtolower($trim) : strtolower($trim)) . ' ';
        $token_count = max(1, count(preg_split('/\s+/', trim($lower))));

        // Sehr kurze Texte: zu wenig Statistik, dann reicht ein hoher Buchstabenanteil.
        if ($token_count < 40) {
            return $letter_ratio >= 0.6;
        }

        // Haeufige Funktionswoerter der unterstuetzten lateinischen Sprachen.
        $common = [
            'der', 'die', 'das', 'und', 'ist', 'von', 'den', 'mit', 'für', 'ein', 'eine', 'auf', 'sich', 'nicht', 'auch',
            'the', 'and', 'of', 'to', 'is', 'in', 'for', 'on', 'with', 'are', 'this', 'that', 'as', 'by',
            'les', 'des', 'une', 'est', 'que', 'pour', 'dans', 'avec', 'sur',
            'los', 'las', 'una', 'para', 'con', 'por', 'del',
            'che', 'per', 'una', 'del', 'gli', 'sono',
            'het', 'een', 'van', 'met', 'voor',
            'dos', 'das', 'uma', 'não', 'com',
        ];
        $hits = 0;
        foreach (array_unique($common) as $w) {
            $hits += preg_match_all('/(?<![\p{L}])' . preg_quote($w, '/') . '(?![\p{L}])/u', $lower);
        }
        $per_1000 = 1000 * $hits / $token_count;

        return $per_1000 >= 12;
    }

    /** Findet alle Streams und dekomprimiert sie (Flate/raw). Nur Text-/CMap-Streams behalten. */
    private function pdf_decode_streams(string $bytes): array {
        $streams = [];
        $offset = 0;
        $len = strlen($bytes);
        while (($start = strpos($bytes, 'stream', $offset)) !== false) {
            $p = $start + 6;
            if ($p < $len && $bytes[$p] === "\r") {
                $p++;
            }
            if ($p < $len && $bytes[$p] === "\n") {
                $p++;
            }
            $end = strpos($bytes, 'endstream', $p);
            if ($end === false) {
                break;
            }
            $raw = substr($bytes, $p, $end - $p);
            $raw = preg_replace('/(\r\n|\r|\n)$/', '', $raw);
            $decoded = $this->pdf_inflate((string) $raw);
            if ($decoded !== '' && preg_match('/BT|Tj|TJ|bfchar|bfrange|begincmap/', $decoded)) {
                $streams[] = $decoded;
            }
            $offset = $end + 9;
        }
        return $streams;
    }

    private function pdf_inflate(string $raw): string {
        $out = @gzuncompress($raw);
        if ($out === false) {
            $out = @gzinflate($raw);
        }
        if ($out === false) {
            $out = @gzdecode($raw);
        }
        if ($out === false) {
            $out = $raw; // unkomprimierter Content-Stream
        }
        return (string) $out;
    }

    /** Baut aus allen ToUnicode-CMaps eine Abbildung Quellcode(hex) -> UTF-8. */
    private function pdf_build_tounicode(array $streams): array {
        $map = [];
        $code_len = 0;

        foreach ($streams as $s) {
            if (strpos($s, 'beginbfchar') === false && strpos($s, 'beginbfrange') === false) {
                continue;
            }

            if (preg_match_all('/beginbfchar(.*?)endbfchar/s', $s, $blocks)) {
                foreach ($blocks[1] as $blk) {
                    if (preg_match_all('/<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>/', $blk, $m, PREG_SET_ORDER)) {
                        foreach ($m as $pair) {
                            $src = strtoupper($pair[1]);
                            $code_len = max($code_len, intdiv(strlen($src), 2));
                            $map[$src] = $this->pdf_hex_to_utf8($pair[2]);
                        }
                    }
                }
            }

            if (preg_match_all('/beginbfrange(.*?)endbfrange/s', $s, $blocks)) {
                foreach ($blocks[1] as $blk) {
                    if (preg_match_all('/<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]+>)/s', $blk, $ranges, PREG_SET_ORDER)) {
                        foreach ($ranges as $r) {
                            $src_len = intdiv(strlen($r[1]), 2);
                            $code_len = max($code_len, $src_len);
                            $lo = hexdec($r[1]);
                            $hi = hexdec($r[2]);
                            if ($hi - $lo > 65535) {
                                continue; // Schutz vor absurden Bereichen
                            }
                            if ($r[3][0] === '[') {
                                preg_match_all('/<([0-9A-Fa-f]+)>/', $r[3], $dm);
                                $i = 0;
                                for ($c = $lo; $c <= $hi && $i < count($dm[1]); $c++, $i++) {
                                    $key = strtoupper(str_pad(dechex($c), $src_len * 2, '0', STR_PAD_LEFT));
                                    $map[$key] = $this->pdf_hex_to_utf8($dm[1][$i]);
                                }
                            } else {
                                $base = hexdec(trim($r[3], '<>'));
                                $n = 0;
                                for ($c = $lo; $c <= $hi; $c++, $n++) {
                                    $key = strtoupper(str_pad(dechex($c), $src_len * 2, '0', STR_PAD_LEFT));
                                    $map[$key] = $this->pdf_codepoint_to_utf8($base + $n);
                                }
                            }
                        }
                    }
                }
            }
        }

        return ['map' => $map, 'code_len' => $code_len ?: 1];
    }

    /** UTF-16BE-Hex (ToUnicode-Ziel) -> UTF-8. */
    private function pdf_hex_to_utf8(string $hex): string {
        $hex = preg_replace('/[^0-9A-Fa-f]/', '', $hex);
        if ($hex === '') {
            return '';
        }
        if (strlen($hex) % 4 !== 0) {
            $hex = str_pad($hex, (int) (ceil(strlen($hex) / 4) * 4), '0', STR_PAD_LEFT);
        }
        $units = str_split($hex, 4);
        $out = '';
        for ($i = 0, $c = count($units); $i < $c; $i++) {
            $cu = hexdec($units[$i]);
            if ($cu >= 0xD800 && $cu <= 0xDBFF && $i + 1 < $c) {
                $lo = hexdec($units[$i + 1]);
                $i++;
                $cp = 0x10000 + (($cu - 0xD800) << 10) + ($lo - 0xDC00);
                $out .= $this->pdf_codepoint_to_utf8($cp);
            } else {
                $out .= $this->pdf_codepoint_to_utf8($cu);
            }
        }
        return $out;
    }

    private function pdf_codepoint_to_utf8(int $cp): string {
        if ($cp <= 0) {
            return '';
        }
        if ($cp < 0x80) {
            return chr($cp);
        }
        if ($cp < 0x800) {
            return chr(0xC0 | ($cp >> 6)) . chr(0x80 | ($cp & 0x3F));
        }
        if ($cp < 0x10000) {
            return chr(0xE0 | ($cp >> 12)) . chr(0x80 | (($cp >> 6) & 0x3F)) . chr(0x80 | ($cp & 0x3F));
        }
        return chr(0xF0 | ($cp >> 18)) . chr(0x80 | (($cp >> 12) & 0x3F))
            . chr(0x80 | (($cp >> 6) & 0x3F)) . chr(0x80 | ($cp & 0x3F));
    }

    /** Liest Text-Show-Operatoren aus einem Content-Stream in Dokumentreihenfolge. */
    private function pdf_tokenize_text(string $content, array $map, int $code_len, array $diff = []): string {
        $has_map = !empty($map);
        $n = strlen($content);
        $i = 0;
        $out = '';
        $gap = '';

        $append = function (string $text) use (&$out, &$gap): void {
            if ($text === '') {
                return;
            }
            if ($out === '') {
                $out = $text;
            } else {
                $sep = (strpos($gap, 'T*') !== false || strpos($gap, 'Td') !== false || strpos($gap, 'TD') !== false)
                    ? "\n"
                    : ' ';
                $out .= $sep . $text;
            }
            $gap = '';
        };

        while ($i < $n) {
            $ch = $content[$i];
            if ($ch === '(') {
                [$raw, $i] = $this->pdf_read_literal($content, $i);
                $append($this->pdf_decode_bytes($raw, $map, $code_len, $has_map, $diff));
                continue;
            }
            if ($ch === '<' && ($i + 1 >= $n || $content[$i + 1] !== '<')) {
                $close = strpos($content, '>', $i);
                if ($close === false) {
                    break;
                }
                $hex = substr($content, $i + 1, $close - $i - 1);
                $append($this->pdf_decode_bytes($this->pdf_hex_to_bytes($hex), $map, $code_len, $has_map, $diff));
                $i = $close + 1;
                continue;
            }
            if ($ch === '[') {
                $close = strpos($content, ']', $i);
                if ($close === false) {
                    break;
                }
                $arr = substr($content, $i + 1, $close - $i - 1);
                $append($this->pdf_decode_array($arr, $map, $code_len, $has_map, $diff));
                $i = $close + 1;
                continue;
            }
            $gap .= $ch;
            $i++;
        }

        return $out;
    }

    /** Liest ab Position $i (auf '(') einen balancierten Literal-String; gibt [bytes, next_i]. */
    private function pdf_read_literal(string $content, int $i): array {
        $n = strlen($content);
        $i++; // ueber '('
        $depth = 1;
        $buf = '';
        while ($i < $n) {
            $ch = $content[$i];
            if ($ch === '\\') {
                $next = $i + 1 < $n ? $content[$i + 1] : '';
                switch ($next) {
                    case 'n': $buf .= "\n"; $i += 2; break;
                    case 'r': $buf .= "\r"; $i += 2; break;
                    case 't': $buf .= "\t"; $i += 2; break;
                    case 'b': $buf .= "\x08"; $i += 2; break;
                    case 'f': $buf .= "\x0C"; $i += 2; break;
                    case '(': $buf .= '('; $i += 2; break;
                    case ')': $buf .= ')'; $i += 2; break;
                    case '\\': $buf .= '\\'; $i += 2; break;
                    case "\r": $i += 2; if ($i < $n && $content[$i] === "\n") { $i++; } break; // Zeilenfortsetzung
                    case "\n": $i += 2; break;
                    default:
                        if ($next !== '' && $next >= '0' && $next <= '7') {
                            $oct = '';
                            $j = $i + 1;
                            while ($j < $n && strlen($oct) < 3 && $content[$j] >= '0' && $content[$j] <= '7') {
                                $oct .= $content[$j];
                                $j++;
                            }
                            $buf .= chr(octdec($oct) & 0xFF);
                            $i = $j;
                        } else {
                            $buf .= $next;
                            $i += 2;
                        }
                }
                continue;
            }
            if ($ch === '(') {
                $depth++;
                $buf .= $ch;
                $i++;
                continue;
            }
            if ($ch === ')') {
                $depth--;
                if ($depth === 0) {
                    $i++;
                    break;
                }
                $buf .= $ch;
                $i++;
                continue;
            }
            $buf .= $ch;
            $i++;
        }
        return [$buf, $i];
    }

    private function pdf_hex_to_bytes(string $hex): string {
        $hex = preg_replace('/[^0-9A-Fa-f]/', '', $hex);
        if (strlen($hex) % 2 !== 0) {
            $hex .= '0';
        }
        return (string) @hex2bin($hex);
    }

    /** Dekodiert eine TJ-Array-Zeichenkette: Strings zusammenfuegen, grosse Kerning-Luecken -> Space. */
    private function pdf_decode_array(string $arr, array $map, int $code_len, bool $has_map, array $diff = []): string {
        $n = strlen($arr);
        $i = 0;
        $out = '';
        while ($i < $n) {
            $ch = $arr[$i];
            if ($ch === '(') {
                [$raw, $i] = $this->pdf_read_literal($arr, $i);
                $out .= $this->pdf_decode_bytes($raw, $map, $code_len, $has_map, $diff);
                continue;
            }
            if ($ch === '<') {
                $close = strpos($arr, '>', $i);
                if ($close === false) {
                    break;
                }
                $out .= $this->pdf_decode_bytes($this->pdf_hex_to_bytes(substr($arr, $i + 1, $close - $i - 1)), $map, $code_len, $has_map, $diff);
                $i = $close + 1;
                continue;
            }
            // Zahl (Kerning): grosse Betraege signalisieren Wortabstand.
            if ($ch === '-' || $ch === '+' || $ch === '.' || ($ch >= '0' && $ch <= '9')) {
                $j = $i;
                while ($j < $n && ($arr[$j] === '-' || $arr[$j] === '+' || $arr[$j] === '.' || ($arr[$j] >= '0' && $arr[$j] <= '9'))) {
                    $j++;
                }
                $num = (float) substr($arr, $i, $j - $i);
                if (abs($num) >= 100 && ($out === '' || substr($out, -1) !== ' ')) {
                    $out .= ' ';
                }
                $i = $j;
                continue;
            }
            $i++;
        }
        return $out;
    }

    /**
     * Wandelt rohe String-Bytes in UTF-8 um. Reihenfolge: ToUnicode-CMap ->
     * /Differences-Abbildung (Glyphnamen) -> CP1252/Latin-1.
     */
    private function pdf_decode_bytes(string $raw, array $map, int $code_len, bool $has_map, array $diff = []): string {
        if ($raw === '') {
            return '';
        }
        if ($has_map && $code_len >= 1) {
            $out = '';
            $len = strlen($raw);
            $step = max(1, $code_len);
            for ($i = 0; $i + $step <= $len; $i += $step) {
                $chunk = substr($raw, $i, $step);
                $key = strtoupper(bin2hex($chunk));
                if (isset($map[$key])) {
                    $out .= $map[$key];
                } elseif ($step === 2 && isset($map[strtoupper(bin2hex($chunk[1]))])) {
                    $out .= $map[strtoupper(bin2hex($chunk[1]))];
                } elseif ($step === 1 && isset($diff[ord($chunk)])) {
                    $out .= $diff[ord($chunk)];
                } else {
                    $out .= $this->pdf_bytes_latin1(($step === 1) ? $chunk : substr($chunk, -1));
                }
            }
            if ($out !== '') {
                return $out;
            }
        }
        // Keine CMap: erst /Differences (byteweise) versuchen, sonst Latin-1.
        if (!empty($diff)) {
            $out = '';
            $len = strlen($raw);
            for ($i = 0; $i < $len; $i++) {
                $b = ord($raw[$i]);
                if (isset($diff[$b])) {
                    $out .= $diff[$b];
                } elseif ($b >= 0x20) {
                    $out .= $this->pdf_bytes_latin1($raw[$i]);
                }
            }
            if ($out !== '') {
                return $out;
            }
        }
        return $this->pdf_bytes_latin1($raw);
    }

    /** CP1252/Latin-1-Bytes -> UTF-8 (Fallback ohne Font-Encoding-Info). */
    private function pdf_bytes_latin1(string $raw): string {
        static $cp1252 = [
            0x80 => 0x20AC, 0x82 => 0x201A, 0x83 => 0x0192, 0x84 => 0x201E, 0x85 => 0x2026,
            0x86 => 0x2020, 0x87 => 0x2021, 0x88 => 0x02C6, 0x89 => 0x2030, 0x8A => 0x0160,
            0x8B => 0x2039, 0x8C => 0x0152, 0x8E => 0x017D, 0x91 => 0x2018, 0x92 => 0x2019,
            0x93 => 0x201C, 0x94 => 0x201D, 0x95 => 0x2022, 0x96 => 0x2013, 0x97 => 0x2014,
            0x98 => 0x02DC, 0x99 => 0x2122, 0x9A => 0x0161, 0x9B => 0x203A, 0x9C => 0x0153,
            0x9E => 0x017E, 0x9F => 0x0178,
        ];
        $out = '';
        $len = strlen($raw);
        for ($i = 0; $i < $len; $i++) {
            $b = ord($raw[$i]);
            if ($b === 0) {
                continue;
            }
            $cp = ($b >= 0x80 && $b <= 0x9F && isset($cp1252[$b])) ? $cp1252[$b] : $b;
            $out .= $this->pdf_codepoint_to_utf8($cp);
        }
        return $out;
    }

    private function insert_document_chunks(string $source_id, string $source_type, string $url, string $title, string $content): int {
        global $wpdb;
        $table = $wpdb->prefix . 'aicb_chunks';
        $chunks = $this->chunk_text($content, $title);
        if (!$chunks) {
            return 0;
        }
        $texts = array_column($chunks, 'content');
        $embeddings = $this->embed_texts($texts);
        $now = gmdate('Y-m-d H:i:s');
        $inserted = 0;

        foreach ($chunks as $idx => $chunk) {
            $embedding = $embeddings[$idx] ?? null;
            if (!$embedding) {
                continue;
            }
            $wpdb->insert($table, [
                'source_id' => $source_id,
                'source_type' => $source_type,
                'source_url' => esc_url_raw($url),
                'title' => $title,
                'section' => $chunk['section'],
                'content' => $chunk['content'],
                'content_hash' => sha1($chunk['content']),
                'embedding' => wp_json_encode($embedding),
                'token_estimate' => $this->estimate_tokens($chunk['content']),
                'updated_at' => $now,
            ], ['%s', '%s', '%s', '%s', '%s', '%s', '%s', '%s', '%d', '%s']);
            $inserted++;
        }
        return $inserted;
    }

    private function post_to_document(WP_Post $post): array {
        $parts = [];
        $title = wp_strip_all_tags(get_the_title($post));
        $parts[] = '# ' . $title;

        if ($this->setting_bool('include_excerpts', true) && trim((string) $post->post_excerpt) !== '') {
            $parts[] = "Auszug:\n" . $this->clean_text($post->post_excerpt);
        }

        $content = $post->post_content;
        $content = strip_shortcodes($content);
        $content = apply_filters('the_content', $content);
        $parts[] = $this->clean_text($content);

        if ($this->setting_bool('include_taxonomies', true)) {
            $terms = $this->post_terms_text($post);
            if ($terms !== '') {
                $parts[] = "Taxonomien:\n" . $terms;
            }
        }

        $parts[] = 'Quelle: ' . get_permalink($post);

        return [
            'title' => $title ?: ('Post ' . $post->ID),
            'url' => get_permalink($post),
            'content' => trim(implode("\n\n", array_filter($parts))),
        ];
    }

    private function post_terms_text(WP_Post $post): string {
        $taxonomies = get_object_taxonomies($post->post_type, 'objects');
        $lines = [];
        foreach ($taxonomies as $taxonomy) {
            if (empty($taxonomy->public)) {
                continue;
            }
            $terms = get_the_terms($post, $taxonomy->name);
            if (!$terms || is_wp_error($terms)) {
                continue;
            }
            $names = array_map(fn($term) => $term->name, $terms);
            $lines[] = $taxonomy->label . ': ' . implode(', ', $names);
        }
        return implode("\n", $lines);
    }

    /**
     * Kleinere Abschnitte mit Ueberlappung. Kleiner heisst: eine konkrete
     * Angabe (Preis, Uhrzeit, Bedingung) dominiert den Chunk und wird bei der
     * Suche auch gefunden. Die Ueberlappung sorgt dafuer, dass ein Detail an
     * der Grenze zweier Abschnitte nicht verloren geht.
     */
    private function chunk_text(string $text, string $title): array {
        $clean = $this->normalize_text($text);
        if ($clean === '') {
            return [];
        }

        $sections = $this->split_sections($clean, $title);
        $chunks = [];
        foreach ($sections as $section) {
            $paragraphs = preg_split("/\n{2,}/", $section['content']) ?: [];
            $buffer = '';
            foreach ($paragraphs as $paragraph) {
                $paragraph = trim($paragraph);
                if ($paragraph === '') {
                    continue;
                }
                $candidate = trim($buffer . "\n\n" . $paragraph);
                if ($buffer !== '' && $this->estimate_tokens($candidate) > self::CHUNK_TARGET_TOKENS) {
                    $chunks[] = [
                        'section' => $section['title'],
                        'content' => $this->format_chunk($title, $section['title'], $buffer),
                    ];
                    // Der letzte Absatz wandert in den naechsten Chunk mit.
                    $overlap = $this->chunk_overlap_tail($buffer);
                    $buffer = trim($overlap === '' ? $paragraph : $overlap . "\n\n" . $paragraph);
                } else {
                    $buffer = $candidate;
                }
            }
            if (trim($buffer) !== '') {
                $chunks[] = [
                    'section' => $section['title'],
                    'content' => $this->format_chunk($title, $section['title'], $buffer),
                ];
            }
        }
        return $chunks;
    }

    /** Letzter Absatz eines Chunks als Ueberlappung fuer den naechsten. */
    private function chunk_overlap_tail(string $buffer): string {
        $parts = preg_split("/\n{2,}/", trim($buffer)) ?: [];
        if (!$parts) {
            return '';
        }
        $tail = trim((string) end($parts));
        if ($tail === '' || $this->estimate_tokens($tail) > self::CHUNK_OVERLAP_TOKENS) {
            // Zu langer Absatz: nur die letzten Saetze mitnehmen.
            $sentences = preg_split('/(?<=[.!?])\s+/u', $tail) ?: [];
            $tail = '';
            while ($sentences && $this->estimate_tokens($tail) < self::CHUNK_OVERLAP_TOKENS) {
                $tail = trim(array_pop($sentences) . ' ' . $tail);
            }
        }
        return trim($tail);
    }

    private function split_sections(string $text, string $default_title): array {
        $lines = preg_split('/\n/', $text) ?: [];
        $sections = [];
        $current = $default_title ?: 'Inhalt';
        $buffer = [];
        foreach ($lines as $line) {
            if (preg_match('/^#{1,6}\s+(.+)$/', trim($line), $m)) {
                if (trim(implode("\n", $buffer)) !== '') {
                    $sections[] = ['title' => $current, 'content' => trim(implode("\n", $buffer))];
                }
                $current = trim($m[1]);
                $buffer = [];
                continue;
            }
            $buffer[] = $line;
        }
        if (trim(implode("\n", $buffer)) !== '') {
            $sections[] = ['title' => $current, 'content' => trim(implode("\n", $buffer))];
        }
        return $sections ?: [['title' => $default_title ?: 'Inhalt', 'content' => $text]];
    }

    private function format_chunk(string $title, string $section, string $body): string {
        return "Document: {$title}\nSection: {$section}\n" . trim($body);
    }

    private function answer_question(string $question, array $history, string $lang, array $offered = []): array {
        global $wpdb;
        $chunks_table = $wpdb->prefix . 'aicb_chunks';
        $count = (int) $wpdb->get_var("SELECT COUNT(*) FROM {$chunks_table}");
        $pack = $this->lang_pack($lang);

        // Auch ohne Index wird geantwortet: Begruessungen und Small Talk sollen
        // funktionieren, statt eine Fehlermeldung auszuwerfen.
        $matches = [];
        if ($count > 0) {
            // Mit Originalfrage und umformulierter Variante suchen: das faengt
            // Folgefragen und fremdsprachige Fragen gleichzeitig ab.
            $queries = [$question];
            $rewritten = $this->rewrite_followup($question, $history);
            if ($rewritten !== '' && $rewritten !== $question) {
                $queries[] = $rewritten;
            }
            $vectors = array_values(array_filter($this->embed_texts($queries)));
            if ($vectors) {
                $matches = $this->search_chunks($vectors);
            }
        }

        // Schwache Treffer fliegen raus - bei "hallo" passt kein Abschnitt und
        // die Tokens waeren verschenkt.
        $relevant = array_values(array_filter(
            $matches,
            fn($row) => (float) ($row['score'] ?? 0) >= self::CONTEXT_MIN_SCORE
        ));

        // Sprache der aktuellen Nachricht schlaegt die Sprache der Website.
        $target_lang = $this->detect_message_lang($question) ?: $lang;
        $context = $relevant ? $this->build_context($relevant) : '';
        $messages = $this->build_chat_messages($question, $history, $context, $target_lang, $count > 0);
        $chat = $this->openai_chat($messages);
        $answer = trim((string) ($chat['answer'] ?? ''));
        if ($answer === '') {
            $answer = $count > 0 ? $pack['error'] : $pack['no_index'];
        }

        $candidates = $this->card_candidates($relevant, $answer);
        $actions = $this->build_actions($candidates, $question, $answer, $history, $target_lang, $relevant, $offered);

        // Aehnlichkeitswerte allein trennen Begruessung und fremdsprachige
        // Fachfrage nicht (gemessen: 0.32 vs 0.31). Deshalb meldet der
        // Button-Call, ob die Antwort ueberhaupt eine inhaltliche Auskunft ist.
        $is_content = $actions['content'] === null ? true : (bool) $actions['content'];
        // Ohne KI-Urteil (kein Key, Call gescheitert) faellt die Karte auf den
        // besten Treffer zurueck; mit Urteil zaehlt allein die Wahl des Modells.
        $card_row = $actions['card'];
        if ($card_row === null && $actions['content'] === null && $candidates) {
            $card_row = $candidates[0];
        }
        $card = ($is_content && $card_row) ? $this->build_card($card_row) : null;

        // Quellenblock in der Sprache der Antwort - der Nutzer darf in jeder
        // Sprache schreiben, der Block muss zur Antwort passen.
        $sources = ($relevant && $is_content) ? $this->sources_from_matches($relevant) : [];
        $answer_has_url = (bool) preg_match('#https?://#i', $answer);
        if ($sources && !$answer_has_url && !$this->has_sources_block($answer)) {
            $answer_pack = $this->lang_pack($target_lang);
            $answer .= "\n\n" . $answer_pack['sources'] . ":\n"
                . implode("\n", array_map(fn($s) => $s['url'], $sources));
        }

        $rich = ['version' => 1, 'actions' => $actions['actions']];
        if ($card) {
            $rich['cards'] = [$card];
        }

        return [
            'answer' => $answer,
            'sources' => $sources,
            'rich' => $rich,
            'usage' => $chat['usage'] ?? [],
        ];
    }

    /** Steht im Antworttext schon ein Quellenblock (in irgendeiner Sprache)? */
    private function has_sources_block(string $answer): bool {
        foreach ($this->sources_labels() as $label) {
            if (stripos($answer, $label . ':') !== false) {
                return true;
            }
        }
        return false;
    }

    /**
     * Kurze Folgefragen ("und die Preise?") fuer die Suche eigenstaendig machen.
     * Nur bei kurzen Nachrichten mit Vorgeschichte - sonst ein Call zu viel.
     */
    private function rewrite_followup(string $question, array $history): string {
        if (!$history || $this->str_len($question) > 90 || str_word_count($question) > 12) {
            return $question;
        }
        $lines = [];
        foreach (array_slice($history, -4) as $item) {
            if (!is_array($item)) {
                continue;
            }
            $role = strtolower((string) ($item['role'] ?? $item['sender'] ?? 'user'));
            $content = trim((string) ($item['content'] ?? $item['text'] ?? ''));
            if ($content === '') {
                continue;
            }
            $lines[] = (in_array($role, ['assistant', 'ai', 'bot'], true) ? 'Assistent: ' : 'Nutzer: ')
                . $this->limit_text($content, 240);
        }
        if (!$lines) {
            return $question;
        }
        try {
            $chat = $this->openai_chat([
                ['role' => 'system', 'content' =>
                    'Du formulierst die letzte Nutzernachricht so um, dass sie ohne den Chatverlauf '
                    . 'verstaendlich ist. Behalte die Sprache der Nachricht. Fuege keine neuen '
                    . 'Informationen hinzu. Ist die Nachricht eine Begruessung oder Small Talk, gib sie '
                    . 'unveraendert zurueck. Antworte nur mit der umformulierten Nachricht.'],
                ['role' => 'user', 'content' => implode("\n", $lines) . "\n\nLetzte Nachricht: " . $question],
            ], ['temperature' => 0, 'max_tokens' => 120]);
            $rewritten = trim((string) ($chat['answer'] ?? ''));
            return $rewritten !== '' ? $rewritten : $question;
        } catch (Throwable $e) {
            return $question;
        }
    }

    /**
     * Kandidaten fuer die Antwortkarte. Ausgewaehlt wird spaeter vom Modell -
     * hier fallen nur die Seiten raus, die als Karte nie Sinn ergeben.
     */
    private function card_candidates(array $matches, string $answer): array {
        if (!$matches || $this->looks_unanswered($answer)) {
            return [];
        }
        $candidates = [];
        $seen = [];
        foreach ($matches as $row) {
            if (count($candidates) >= 3) {
                break;
            }
            // Bei Begruessungen und Small Talk passt kein Abschnitt wirklich.
            if ((float) ($row['score'] ?? 0) < self::CARD_MIN_SCORE) {
                continue;
            }
            $url = esc_url_raw((string) ($row['source_url'] ?? ''));
            $title = trim((string) ($row['title'] ?? ''));
            // Ohne Ziel und ohne Titel ist eine Karte wertlos.
            if ($url === '' || $title === '' || isset($seen[$url])) {
                continue;
            }
            if ($this->is_boilerplate_page($title, $url)) {
                continue;
            }
            $seen[$url] = true;
            $candidates[] = $row;
        }
        return $candidates;
    }

    /**
     * Seiten, die als Antwortkarte nie weiterhelfen: Rechtstexte, Startseite,
     * Archive, Konto- und Shop-Funktionsseiten.
     */
    private function is_boilerplate_page(string $title, string $url): bool {
        $haystack = $this->str_lower($title . ' ' . $url);
        $markers = [
            'impressum', 'datenschutz', 'privacy', 'agb', 'terms', 'cookie', 'sitemap',
            'widerruf', 'disclaimer', 'haftung', 'newsletter', 'login', 'anmelden',
            'warenkorb', 'checkout', 'kasse', 'mein-konto', 'my-account', 'suche', 'search',
            '404', 'blog/page', 'category/', 'tag/', 'author/',
        ];
        foreach ($markers as $marker) {
            if (strpos($haystack, $marker) !== false) {
                return true;
            }
        }
        // Startseite: nichts, was man als Detailseite verlinken moechte.
        $path = trim((string) wp_parse_url($url, PHP_URL_PATH), '/');
        return $path === '';
    }

    /** Aus einem Treffer die fertige Karte bauen. */
    private function build_card(array $row): ?array {
        $title = trim((string) ($row['title'] ?? ''));
        $section = trim((string) ($row['section'] ?? ''));
        $url = esc_url_raw((string) ($row['source_url'] ?? ''));
        if ($title === '' || $url === '') {
            return null;
        }

        $card = [
            'title' => $title,
            'description' => $this->card_teaser((string) ($row['content'] ?? ''), $title, $section),
            'details' => [],
            'url' => $url,
        ];
        $image = $this->card_image((string) ($row['source_id'] ?? ''), $url);
        if ($image !== '') {
            $card['image_url'] = $image;
        }
        if ($section !== '' && $section !== $title) {
            $card['details'][] = $section;
        }
        return $card;
    }

    private function card_teaser(string $content, string $title, string $section): string {
        $lines = preg_split('/\n/', $content) ?: [];
        $body = [];
        foreach ($lines as $line) {
            $trimmed = trim($line);
            // Die Chunk-Kopfzeilen und die Ueberschriften selbst sind kein Teaser.
            if ($trimmed === '' || stripos($trimmed, 'Document:') === 0 || stripos($trimmed, 'Section:') === 0) {
                continue;
            }
            if ($trimmed === $title || $trimmed === $section || preg_match('/^#{1,6}\s/', $trimmed)) {
                continue;
            }
            $body[] = $trimmed;
            if (strlen(implode(' ', $body)) > 200) {
                break;
            }
        }
        $teaser = trim(implode(' ', $body));
        if ($teaser === '') {
            return '';
        }
        if (strlen($teaser) > 170) {
            $cut = substr($teaser, 0, 170);
            $space = strrpos($cut, ' ');
            $teaser = ($space ? substr($cut, 0, $space) : $cut) . '...';
        }
        return sanitize_text_field($teaser);
    }

    private function card_image(string $source_id, string $url): string {
        if (strpos($source_id, 'post:') === 0) {
            $post_id = absint(substr($source_id, 5));
            if ($post_id && has_post_thumbnail($post_id)) {
                return (string) get_the_post_thumbnail_url($post_id, 'medium');
            }
        }
        $post_id = $url !== '' ? url_to_postid($url) : 0;
        if ($post_id && has_post_thumbnail($post_id)) {
            return (string) get_the_post_thumbnail_url($post_id, 'medium');
        }
        return '';
    }

    private function looks_unanswered(string $answer): bool {
        $plain = trim(strtolower($answer));
        if ($plain === '') {
            return true;
        }
        $markers = [
            'keine passenden informationen', 'keine informationen', 'weiss ich nicht', 'weiß ich nicht',
            'nicht im kontext', 'kann ich nicht beantworten', 'liegen mir nicht vor',
            'no relevant information', 'i do not know', "i don't know", 'not in the context',
        ];
        foreach ($markers as $marker) {
            if (strpos($plain, $marker) !== false) {
                return true;
            }
        }
        return false;
    }

    /**
     * Nachrichtenliste fuer die Chat-API. Der Verlauf wird als echte Rollen
     * uebergeben, damit sich der Bot wie ein normaler Chat verhaelt und
     * Folgefragen versteht.
     */
    private function build_chat_messages(string $question, array $history, string $context, string $lang, bool $has_index): array {
        $behaviour = "Verhalten:\n"
            . "- Du fuehrst ein normales, fortlaufendes Gespraech. Begruessungen, Dank, Small Talk und "
            . "Fragen zu dir selbst beantwortest du kurz, freundlich und direkt.\n"
            . "- Bei Begruessung oder Small Talk nennst du keine Quellen und sagst nicht, dass "
            . "Informationen fehlen. Frage stattdessen freundlich, wobei du helfen kannst.\n"
            . "- Inhaltliche Fragen zu dieser Website, dem Unternehmen, den Produkten oder Leistungen "
            . "beantwortest du ausschliesslich mit dem bereitgestellten Kontext. Steht die Information "
            . "dort nicht, sage klar, dass du sie nicht hast - erfinde nichts und nutze kein Weltwissen.\n"
            . "- Beziehe dich auf den bisherigen Verlauf. Folgefragen wie \"und die Preise?\" beziehen "
            . "sich auf das zuletzt besprochene Thema.\n"
            . "- Nennst du Fakten aus dem Kontext, gib die passenden Quellen als direkte URLs an.\n"
            . "- Nenne konkrete Details aus dem Kontext: Zahlen, Preise, Uhrzeiten, Dauer, Namen, "
            . "Bedingungen, Ausstattung. Fasse nicht vage zusammen, wenn genaue Angaben dastehen.\n"
            . "- Stehen mehrere Varianten im Kontext (z. B. mehrere Zimmer, Tarife oder Pakete), "
            . "nenne sie einzeln mit ihren jeweiligen Angaben statt nur einer Sammelaussage.\n"
            . "- Halte Antworten kompakt: kurze Absaetze, bei Aufzaehlungen Listen.";

        $target_name = $this->lang_display_name($lang);
        $language_rule = "MANDATORY LANGUAGE RULE - this overrides every other instruction:\n"
            . "- The user's current message is written in " . $target_name . ".\n"
            . "- Write the ENTIRE answer in " . $target_name . ".\n"
            . "- Never switch to another language, not even if the context, the system prompt or the "
            . "previous chat history are written in a different language.\n"
            . "- Translate facts from the context into that language. Keep proper nouns, product "
            . "names, prices, URLs and e-mail addresses unchanged.";

        $messages = [[
            'role' => 'system',
            'content' => $this->setting('system_prompt', self::default_system_prompt())
                . "\n\n" . $behaviour . "\n\n" . $language_rule,
        ]];

        foreach (array_slice($history, -10) as $item) {
            if (!is_array($item)) {
                continue;
            }
            $role = sanitize_key((string) ($item['role'] ?? $item['sender'] ?? 'user'));
            $content = sanitize_textarea_field((string) ($item['content'] ?? $item['text'] ?? ''));
            if ($content === '') {
                continue;
            }
            $messages[] = [
                'role' => in_array($role, ['assistant', 'ai', 'bot'], true) ? 'assistant' : 'user',
                'content' => $this->limit_text($content, 1200),
            ];
        }

        if ($context !== '') {
            $context_note = "Kontext aus dieser Website (nur fuer inhaltliche Fragen verwenden):\n" . $context;
        } elseif ($has_index) {
            $context_note = 'Kontext aus dieser Website: keine passenden Abschnitte gefunden. '
                . 'Bei einer inhaltlichen Frage sage das offen; bei Small Talk antworte einfach normal.';
        } else {
            $context_note = 'Kontext aus dieser Website: der Index ist noch leer. '
                . 'Beantworte inhaltliche Fragen nicht aus dem Gedaechtnis, sondern sage, dass du dazu '
                . 'noch keine Informationen hast; Small Talk beantworte normal.';
        }

        $messages[] = [
            'role' => 'user',
            'content' => $context_note . "\n\nNachricht des Nutzers: " . $question
                . "\n\n(Reminder: answer in " . $target_name . ".)",
        ];

        return $messages;
    }

    /**
     * @param array $query_vectors Ein Vektor oder eine Liste von Vektoren. Bei
     *                             mehreren zaehlt pro Abschnitt der beste Treffer.
     */
    private function search_chunks(array $query_vectors): array {
        global $wpdb;
        $table = $wpdb->prefix . 'aicb_chunks';
        $limit = max(1, min(24, (int) $this->setting('retriever_k', 12)));
        // Einzelvektor auch akzeptieren, damit Aufrufer beides uebergeben koennen.
        $vectors = (isset($query_vectors[0]) && is_array($query_vectors[0])) ? $query_vectors : [$query_vectors];
        $rows = $wpdb->get_results("SELECT id, source_id, source_url, title, section, content, embedding FROM {$table} WHERE embedding IS NOT NULL", ARRAY_A);
        $scored = [];
        foreach ($rows ?: [] as $row) {
            $vector = json_decode((string) $row['embedding'], true);
            if (!is_array($vector)) {
                continue;
            }
            $best = 0.0;
            foreach ($vectors as $query_vector) {
                $score = $this->cosine_similarity($query_vector, $vector);
                if ($score > $best) {
                    $best = $score;
                }
            }
            $row['score'] = $best;
            unset($row['embedding']);
            $scored[] = $row;
        }
        usort($scored, fn($a, $b) => ($b['score'] <=> $a['score']));
        $top = array_slice($scored, 0, $limit);
        return $this->with_neighbour_chunks($top, $scored, $limit);
    }

    /**
     * Zu jedem Treffer den direkt angrenzenden Abschnitt derselben Seite
     * ergaenzen. Details stehen oft eine Zeile weiter: die Tabelle im einen
     * Chunk, die Fussnote mit den Bedingungen im naechsten.
     */
    private function with_neighbour_chunks(array $top, array $all, int $limit): array {
        if (!$top) {
            return $top;
        }
        $by_id = [];
        foreach ($all as $row) {
            $by_id[(int) $row['id']] = $row;
        }
        $selected = [];
        foreach ($top as $row) {
            $selected[(int) $row['id']] = $row;
        }
        foreach ($top as $row) {
            if (count($selected) >= $limit + 6) {
                break;
            }
            $id = (int) $row['id'];
            foreach ([$id - 1, $id + 1] as $neighbour_id) {
                if (isset($selected[$neighbour_id]) || !isset($by_id[$neighbour_id])) {
                    continue;
                }
                $neighbour = $by_id[$neighbour_id];
                // Nur innerhalb derselben Seite.
                if ((string) $neighbour['source_id'] !== (string) $row['source_id']) {
                    continue;
                }
                // Etwas unter den Treffer einsortieren, damit die Reihenfolge stimmt.
                $neighbour['score'] = (float) $row['score'] - 0.001;
                $selected[$neighbour_id] = $neighbour;
            }
        }
        $result = array_values($selected);
        usort($result, fn($a, $b) => ($b['score'] <=> $a['score']));
        return $result;
    }

    private function build_context(array $matches): string {
        $max = max(3000, (int) $this->setting('max_context_chars', 20000));
        $parts = [];
        $chars = 0;
        foreach ($matches as $idx => $row) {
            $content = trim((string) $row['content']);
            if ($content === '') {
                continue;
            }
            if (strlen($content) > 3000) {
                $content = substr($content, 0, 3000) . "\n...[gekuerzt]";
            }
            $entry = '[' . ($idx + 1) . '] Quelle: ' . ($row['source_url'] ?: home_url('/')) . ' | Titel: ' . $row['title'] . ' | Abschnitt: ' . $row['section'] . "\n" . $content;
            if ($chars + strlen($entry) > $max && $parts) {
                break;
            }
            $parts[] = $entry;
            $chars += strlen($entry);
        }
        return implode("\n\n", $parts);
    }

    private function sources_from_matches(array $matches): array {
        $seen = [];
        $sources = [];
        foreach ($matches as $row) {
            $url = esc_url_raw((string) ($row['source_url'] ?: home_url('/')));
            if (!$url || isset($seen[$url])) {
                continue;
            }
            $seen[$url] = true;
            $sources[] = [
                'title' => sanitize_text_field((string) $row['title']),
                'section' => sanitize_text_field((string) $row['section']),
                'url' => $url,
                'score' => isset($row['score']) ? round((float) $row['score'], 4) : null,
            ];
        }
        return array_slice($sources, 0, 5);
    }

    private function openai_chat(array $messages, array $overrides = []): array {
        $payload = array_merge([
            'model' => $this->setting('chat_model', 'gpt-4o-mini'),
            'temperature' => 0,
            'messages' => $messages,
        ], $overrides);
        $data = $this->openai_request('chat/completions', $payload);
        return [
            'answer' => $data['choices'][0]['message']['content'] ?? '',
            'usage' => $data['usage'] ?? [],
        ];
    }

    private function embed_text(string $text): array {
        $vectors = $this->embed_texts([$text]);
        return $vectors[0] ?? [];
    }

    private function embed_texts(array $texts): array {
        $texts = array_values(array_filter(array_map(fn($t) => trim((string) $t), $texts), fn($t) => $t !== ''));
        if (!$texts) {
            return [];
        }
        $data = $this->openai_request('embeddings', [
            'model' => $this->setting('embedding_model', 'text-embedding-3-large'),
            'input' => $texts,
        ]);
        $vectors = [];
        foreach (($data['data'] ?? []) as $item) {
            $vectors[(int) $item['index']] = $item['embedding'];
        }
        ksort($vectors);
        return array_values($vectors);
    }

    private function openai_request(string $path, array $payload): array {
        $api_key = trim((string) $this->setting('openai_api_key', ''));
        if ($api_key === '') {
            throw new RuntimeException('OpenAI API Key fehlt. Bitte im Plugin speichern.');
        }

        $response = wp_remote_post('https://api.openai.com/v1/' . ltrim($path, '/'), [
            'timeout' => 60,
            'headers' => [
                'Authorization' => 'Bearer ' . $api_key,
                'Content-Type' => 'application/json',
            ],
            'body' => wp_json_encode($payload),
        ]);

        if (is_wp_error($response)) {
            throw new RuntimeException($response->get_error_message());
        }
        $code = (int) wp_remote_retrieve_response_code($response);
        $body = (string) wp_remote_retrieve_body($response);
        $data = json_decode($body, true);
        if ($code < 200 || $code >= 300) {
            $message = $data['error']['message'] ?? substr($body, 0, 300);
            throw new RuntimeException('OpenAI Fehler ' . $code . ': ' . $message);
        }
        if (!is_array($data)) {
            throw new RuntimeException('OpenAI Antwort konnte nicht gelesen werden.');
        }
        return $data;
    }

    private function create_session_payload(): array {
        global $wpdb;
        $token = wp_generate_password(48, false, false);
        $hash = $this->hash_token($token);
        $now = gmdate('Y-m-d H:i:s');
        $expires = gmdate('Y-m-d H:i:s', time() + self::SESSION_TTL);
        $wpdb->insert($wpdb->prefix . 'aicb_sessions', [
            'token_hash' => $hash,
            'expires_at' => $expires,
            'created_at' => $now,
            'last_seen_at' => $now,
            'ip_hash' => $this->request_ip_hash(),
            'user_agent' => substr(sanitize_text_field((string) ($_SERVER['HTTP_USER_AGENT'] ?? '')), 0, 255),
            'messages' => 0,
        ], ['%s', '%s', '%s', '%s', '%s', '%s', '%d']);
        return [
            'token' => $token,
            'session_hash' => $hash,
            'expires_at' => gmdate('c', time() + self::SESSION_TTL),
        ];
    }

    private function ensure_session_payload(string $token): array {
        global $wpdb;
        $token = trim($token);
        if ($token !== '') {
            $hash = $this->hash_token($token);
            $row = $wpdb->get_row($wpdb->prepare(
                "SELECT token_hash, expires_at FROM {$wpdb->prefix}aicb_sessions WHERE token_hash = %s AND expires_at > %s",
                $hash,
                gmdate('Y-m-d H:i:s')
            ), ARRAY_A);
            if ($row) {
                return [
                    'token' => $token,
                    'session_hash' => $hash,
                    'expires_at' => gmdate('c', strtotime((string) $row['expires_at'])),
                ];
            }
        }
        return $this->create_session_payload();
    }

    private function touch_session(string $hash): void {
        global $wpdb;
        $wpdb->query($wpdb->prepare(
            "UPDATE {$wpdb->prefix}aicb_sessions SET last_seen_at = %s, messages = messages + 1 WHERE token_hash = %s",
            gmdate('Y-m-d H:i:s'),
            $hash
        ));
    }

    private function record_event(?string $session_hash, string $question, ?string $answer, string $status, ?string $error, array $usage): int {
        global $wpdb;
        $wpdb->insert($wpdb->prefix . 'aicb_events', [
            'session_hash' => $session_hash,
            'user_id' => get_current_user_id() ?: null,
            'question' => $question,
            'answer' => $answer,
            'status' => $status,
            'error' => $error,
            'input_tokens' => (int) ($usage['prompt_tokens'] ?? 0),
            'output_tokens' => (int) ($usage['completion_tokens'] ?? 0),
            'model' => $this->setting('chat_model', 'gpt-4o-mini'),
            'feedback' => 0,
            'created_at' => gmdate('Y-m-d H:i:s'),
        ], ['%s', '%d', '%s', '%s', '%s', '%s', '%d', '%d', '%s', '%d', '%s']);
        return (int) $wpdb->insert_id;
    }

    /**
     * Buttons unter der Antwort. Zuerst von der KI aus dem Gespraech erzeugt,
     * bei Fehlern die statischen Texte.
     */
    private function build_actions(array $candidates, string $question, string $answer, array $history, string $lang, array $matches, array $offered = []): array {
        try {
            $result = $this->ai_quick_actions($candidates, $question, $answer, $history, $lang, $matches, $offered);
            if (!empty($result['actions'])) {
                return $result;
            }
        } catch (Throwable $e) {
            error_log('AICB action generation failed: ' . $e->getMessage());
        }
        return ['actions' => $this->quick_actions($lang), 'lang' => $lang, 'content' => null, 'card' => null];
    }

    private function quick_actions(string $lang = 'de'): array {
        $settings = $this->settings();
        $pack = $this->lang_pack($lang);
        $actions = [];
        if (!empty($settings['contact_url'])) {
            $actions[] = [
                'label' => $pack['action_contact'],
                'type' => 'link',
                'url' => esc_url_raw((string) $settings['contact_url']),
            ];
        } elseif (!empty($settings['contact_email'])) {
            $actions[] = [
                'label' => $pack['action_email'],
                'type' => 'link',
                'url' => 'mailto:' . sanitize_email((string) $settings['contact_email']),
            ];
        }
        $actions[] = [
            'label' => $pack['action_details'],
            'type' => 'question',
            'question' => $pack['action_details_q'],
        ];
        return $actions;
    }

    private const AI_ACTIONS_SYSTEM = <<<'PROMPT'
WICHTIGSTE FORMREGEL: label ist eine Menue-Beschriftung aus ZWEI bis DREI Woertern. Zaehle die Woerter, bevor du antwortest. Vier oder mehr Woerter sind verboten, ebenso Fragen und ganze Saetze.

Du erzeugst die Klick-Buttons, die unter der Antwort eines Chat-Assistenten auf einer Firmen-Website stehen.
Regeln:
- Zwei bis drei Buttons, die genau zu diesem Gespraech passen und den Nutzer einen Schritt weiterbringen.
- Jeder Button oeffnet ein NEUES Thema, das im bisherigen Gespraech noch nicht vorkam. Wiederhole nie die aktuelle Frage, eine fruehere Frage oder den Inhalt der Antwort - auch nicht anders formuliert.
- Die Buttons sollen neugierig machen: nenne Themen, die der Nutzer wahrscheinlich als naechstes interessant findet.
- label: zwei bis drei Woerter, hoechstens 24 Zeichen. Es ist eine Menue-Beschriftung, keine Frage und kein Satz. Gut: "Zimmer ansehen", "Preise & Pauschalen", "Anfahrt", "Termin anfragen". Schlecht: "Wo befindet sich die Zentrale?", "Ich moechte mehr wissen". Keine Emojis, kein Satzzeichen am Ende, niemals abgeschnittene Wortgruppen.
- question: die Nachricht, die beim Klick als Nutzerfrage gesendet wird - ein vollstaendiger, eigenstaendig verstaendlicher Satz.
- SPRACHE, wichtigste Regel: Schreibe ALLE Werte von label und question ausschliesslich in der Sprache, die im Kontext unter "Sprache der Buttons" steht. Diese Anweisung ist auf Deutsch, das aendert daran nichts; auch aeltere Nachrichten im Gespraech aendern daran nichts. Ist die Sprache Tuerkisch, heisst ein Anruf-Button "Ara" und nicht "Anrufen".
- Erfinde nichts: keine Preise, Zahlen, Angebote, Telefonnummern oder URLs, die nicht im Kontext stehen.
- Aktions-Buttons (type "link") nur mit einem target, das der Kontext unter "Verfuegbare Aktionen" auflistet: "card", "contact", "phone" oder "email". Die Adresse setzt das System, du gibst nie eine URL oder Nummer aus. Bei type "link" kein question angeben.
- Wenn "phone" verfuegbar ist und Anrufen im Gespraech sinnvoll waere (Beratung, Termin, dringende Rueckfrage, Kontaktwunsch), setze einen Anruf-Button an die erste Stelle; das Label nennt das Anrufen.
- Hoechstens zwei Aktions-Buttons, jedes target nur einmal.
- Mindestens ein Button muss type "question" sein und unter den Aktions-Buttons stehen.
- Keine zwei Buttons mit gleicher Bedeutung.
Gib zusaetzlich an:
- "card": Nummer der Seite aus "Verfuegbare Seiten", die als Karte unter der Antwort erscheinen soll.
  Waehle nur eine Seite, die genau das Thema der Antwort vertieft und dem Nutzer echten Mehrwert bringt.
  Passt keine Seite wirklich zum Inhalt der Antwort, ist die Antwort allgemein, eine Begruessung oder eine
  Absage, gib 0 an. Im Zweifel immer 0 - eine unpassende Karte ist schlechter als keine.
- "lang": ISO-639-1-Code der Sprache, in der die Antwort des Assistenten geschrieben ist.
- "content": true, wenn die Antwort eine inhaltliche Auskunft zu Website, Unternehmen, Produkten oder Leistungen gibt. false, wenn sie nur Begruessung, Dank, Small Talk, Rueckfrage oder die Aussage ist, dass keine Informationen vorliegen.
Antworte ausschliesslich mit JSON in dieser Form: {"card": 0, "lang": "de", "content": true, "actions": [{"label": "...", "type": "question", "question": "..."}]}
PROMPT;

    private function ai_quick_actions(array $candidates, string $question, string $answer, array $history, string $lang, array $matches, array $offered = []): array {
        $settings = $this->settings();
        if (trim((string) ($settings['openai_api_key'] ?? '')) === '') {
            return ['actions' => [], 'lang' => '', 'content' => null, 'card' => null];
        }

        $sources = $this->sources_text($matches);
        $contact_page = esc_url_raw((string) ($settings['contact_url'] ?? ''));
        $configured_phone = $this->normalize_phone((string) ($settings['contact_phone'] ?? ''));
        $configured_email = sanitize_email((string) ($settings['contact_email'] ?? ''));

        // Nummer/Adresse aus der Antwort nur, wenn sie in den Quellen belegt ist -
        // sonst waehlt der Button eine erfundene Nummer.
        $phone = $this->phone_from_text($answer);
        if ($phone !== '' && !$this->phone_in_sources($phone, $sources)) {
            $phone = '';
        }
        $email = $this->email_from_text($answer);
        if ($email !== '' && stripos($sources, $email) === false) {
            $email = '';
        }
        $phone = $phone !== '' ? $phone : $configured_phone;
        $email = $email !== '' ? $email : $configured_email;

        $targets = [
            'card' => '',
            'contact' => $contact_page,
            'phone' => $phone !== '' ? 'tel:' . $phone : '',
            'email' => $email !== '' ? 'mailto:' . $email : '',
        ];

        $available = array_filter([
            $candidates ? 'card (oeffnet die Seite, die du unter "card" auswaehlst)' : '',
            $targets['contact'] !== '' ? 'contact (Kontaktseite des Unternehmens)' : '',
            $phone !== '' ? 'phone (waehlt ' . $phone . ' direkt auf dem Geraet)' : '',
            $email !== '' ? 'email (oeffnet eine Mail an ' . $email . ')' : '',
        ]);

        $history_lines = [];
        foreach (array_slice($history, -4) as $item) {
            if (!is_array($item)) {
                continue;
            }
            $role = strtolower((string) ($item['role'] ?? $item['sender'] ?? 'user'));
            $content = trim((string) ($item['content'] ?? $item['text'] ?? ''));
            if ($content === '') {
                continue;
            }
            $speaker = in_array($role, ['assistant', 'ai', 'bot'], true) ? 'Assistent' : 'Nutzer';
            $history_lines[] = $speaker . ': ' . $this->limit_text($content, 240);
        }

        $topics = [];
        foreach ((array) ($this->public_widget_config()['topics'] ?? []) as $topic) {
            $label = trim((string) ($topic['label'] ?? ''));
            if ($label !== '') {
                $topics[] = $label;
            }
        }

        $context = ['Sprache der Buttons: ' . $this->lang_display_name($lang)];
        $context[] = 'Verfuegbare Aktionen (die Klammertexte sind nur Erklaerungen, nie als Label '
            . 'uebernehmen): ' . ($available ? implode(', ', $available) : 'keine');
        if ($history_lines) {
            $context[] = "Bisheriges Gespraech:\n" . implode("\n", $history_lines);
        }
        if ($topics) {
            $context[] = 'Themen des Unternehmens: ' . implode(', ', array_slice($topics, 0, 8));
        }
        if ($candidates) {
            $lines = [];
            foreach ($candidates as $idx => $row) {
                $path = (string) wp_parse_url((string) $row['source_url'], PHP_URL_PATH);
                $lines[] = ($idx + 1) . ') ' . trim((string) $row['title'])
                    . (trim((string) ($row['section'] ?? '')) !== '' ? ' - Abschnitt: ' . $row['section'] : '')
                    . ' - ' . ($path ?: $row['source_url']);
            }
            $context[] = "Verfuegbare Seiten (fuer \"card\"):\n" . implode("\n", $lines);
        } else {
            $context[] = 'Verfuegbare Seiten: keine - "card" muss 0 sein.';
        }
        $shown = array_slice(array_filter(array_map('trim', $offered)), -10);
        if ($shown) {
            $context[] = "Diese Buttons wurden im Gespraech schon angezeigt - biete keinen davon noch "
                . "einmal an, auch nicht anders formuliert:\n- " . implode("\n- ", $shown);
        }
        $context[] = 'Aktuelle Frage des Nutzers: ' . $this->limit_text($question, 300);
        $context[] = "Antwort des Assistenten:\n" . $this->limit_text($this->strip_sources_tail($answer), 900);

        $chat = $this->openai_chat([
            ['role' => 'system', 'content' => self::AI_ACTIONS_SYSTEM],
            ['role' => 'user', 'content' => implode("\n\n", $context)],
        ], ['temperature' => 0.4, 'max_tokens' => 260]);

        $payload = $this->decode_json_object((string) ($chat['answer'] ?? ''));
        if (!$payload || !is_array($payload['actions'] ?? null)) {
            return ['actions' => [], 'lang' => '', 'content' => null, 'card' => null];
        }
        $detected = self::normalize_lang((string) ($payload['lang'] ?? ''));
        $is_content = array_key_exists('content', $payload) ? (bool) $payload['content'] : null;

        // Karte: nur die vom Modell gewaehlte Seite, sonst keine.
        $choice = (int) ($payload['card'] ?? 0);
        $chosen = ($choice >= 1 && $choice <= count($candidates)) ? $candidates[$choice - 1] : null;
        if ($chosen) {
            $targets['card'] = esc_url_raw((string) $chosen['source_url']);
        }

        // Aktionen stehen vor den Folgefragen - sie gehoeren optisch zur Karte.
        $links = [];
        $questions = [];
        $used = [];
        // Wortmengen der bisherigen Fragen und der schon gezeigten Buttons.
        $asked_sets = [$this->compare_tokens($question)];
        foreach (array_slice($history, -6) as $item) {
            if (!is_array($item)) {
                continue;
            }
            $role = strtolower((string) ($item['role'] ?? $item['sender'] ?? 'user'));
            if (in_array($role, ['assistant', 'ai', 'bot'], true)) {
                continue;
            }
            $content = trim((string) ($item['content'] ?? $item['text'] ?? ''));
            if ($content !== '') {
                $asked_sets[] = $this->compare_tokens($content);
            }
        }
        $shown_sets = [];
        foreach ($shown as $label) {
            $shown_sets[] = $this->compare_tokens($label);
        }
        foreach (array_slice($payload['actions'], 0, 6) as $item) {
            if (!is_array($item)) {
                continue;
            }
            $label = $this->clean_action_label((string) ($item['label'] ?? ''));
            if ($label === '') {
                continue;
            }
            $type = strtolower(trim((string) ($item['type'] ?? 'question')));
            $target = strtolower(trim((string) ($item['target'] ?? $item['url_ref'] ?? '')));
            // Das Modell schreibt oft "type": "phone" statt type link + target phone.
            if ($target === '' && array_key_exists($type, $targets)) {
                $target = $type;
                $type = 'link';
            }
            if ($type === 'link') {
                $url = $targets[$target] ?? '';
                // Modell-URLs werden nie uebernommen - nur die bekannten Ziele.
                if ($url === '' || isset($used[$target]) || count($links) >= 2) {
                    continue;
                }
                $links[] = ['label' => $label, 'type' => 'link', 'url' => $url];
                $used[$target] = true;
                continue;
            }
            if (count($questions) >= 3) {
                continue;
            }
            $follow_up = trim(preg_replace('/\s+/', ' ', (string) ($item['question'] ?? '')));
            // Ohne Fragetext ist es ein missglueckter Aktions-Button ("Anrufen").
            if ($this->str_len($follow_up) < 6) {
                continue;
            }
            // Kein Button, der wiederholt, was schon gefragt oder gezeigt wurde.
            if ($this->is_repeat_action($follow_up, $asked_sets)
                || $this->is_repeat_action($label, $shown_sets, 0.6, 1)) {
                continue;
            }
            $questions[] = [
                'label' => $label,
                'type' => 'question',
                'question' => $this->limit_text($follow_up, 180),
            ];
        }

        $actions = array_merge($links, $questions);
        $seen = [];
        $unique = [];
        foreach ($actions as $action) {
            $key = $this->str_lower($action['label']);
            if (isset($seen[$key])) {
                continue;
            }
            $seen[$key] = true;
            $unique[] = $action;
        }
        return [
            'actions' => array_slice($unique, 0, 3),
            'lang' => $detected,
            'content' => $is_content,
            'card' => $chosen,
        ];
    }

    /**
     * mbstring ist nicht auf jedem Hoster installiert - ohne Fallback waere ein
     * Fatal Error im Chat die Folge.
     */
    private function str_len(string $value): int {
        return function_exists('mb_strlen') ? mb_strlen($value) : strlen($value);
    }

    private function str_cut(string $value, int $start, ?int $length = null): string {
        if (function_exists('mb_substr')) {
            return mb_substr($value, $start, $length);
        }
        return $length === null ? substr($value, $start) : substr($value, $start, $length);
    }

    private function str_lower(string $value): string {
        return function_exists('mb_strtolower') ? mb_strtolower($value) : strtolower($value);
    }

    private function str_rpos(string $value, string $needle) {
        return function_exists('mb_strrpos') ? mb_strrpos($value, $needle) : strrpos($value, $needle);
    }

    private function sources_text(array $matches): string {
        $parts = [];
        foreach (array_slice($matches, 0, 6) as $row) {
            foreach (['content', 'title', 'section', 'source_url'] as $key) {
                $value = (string) ($row[$key] ?? '');
                if ($value !== '') {
                    $parts[] = $value;
                }
            }
        }
        return implode("\n", $parts);
    }

    private function normalize_phone(string $raw): string {
        $cleaned = preg_replace('/[^\d+]/', '', $raw);
        if (strpos($cleaned, '00') === 0) {
            $cleaned = '+' . substr($cleaned, 2);
        }
        $digits = ltrim($cleaned, '+');
        if ($digits === '' || !ctype_digit($digits) || strlen($digits) < 7 || strlen($digits) > 15) {
            return '';
        }
        return $cleaned;
    }

    /**
     * Erste plausible Telefonnummer. Akzeptiert wird eine Zahlenfolge nur mit
     * Landesvorwahl, mit Hinweiswort davor oder als gegliederte Rufnummer -
     * sonst landen Preise, Jahreszahlen und Uhrzeiten im Anruf-Button.
     */
    private function phone_from_text(string $text): string {
        if (!preg_match_all('/\+?\d[\d\s().\-\/]{5,}\d/', $text, $found, PREG_OFFSET_CAPTURE)) {
            return '';
        }
        $cue = '/(?:tel|telefon|telephone|fon|phone|mobil|handy|hotline|zentrale|durchwahl|festnetz|whatsapp|anruf|anrufen|rufen sie|ruf uns|erreichbar|erreichst|erreichen sie|erreichen|call us|call|nummer|number)[^0-9+]{0,20}$/i';
        foreach ($found[0] as $match) {
            $candidate = (string) $match[0];
            $offset = (int) $match[1];
            $number = $this->normalize_phone($candidate);
            if ($number === '') {
                continue;
            }
            $before = substr($text, max(0, $offset - 40), min(40, $offset));
            $grouped = strpos($candidate, '/') !== false && strlen(ltrim($number, '+')) >= 9;
            if (strpos($number, '+') === 0 || $grouped || preg_match($cue, $before)) {
                return $number;
            }
        }
        return '';
    }

    /**
     * Verglichen wird gegen echte Nummern-Kandidaten im Quelltext, nicht gegen
     * alle Ziffern am Stueck - sonst gilt eine erfundene Nummer als belegt.
     */
    private function phone_in_sources(string $number, string $sources): bool {
        $digits = preg_replace('/\D/', '', $number);
        if (strlen($digits) < 7) {
            return false;
        }
        $tail = substr($digits, -7);
        if (!preg_match_all('/\+?\d[\d\s().\-\/]{5,}\d/', $sources, $found)) {
            return false;
        }
        foreach ($found[0] as $candidate) {
            $clean = preg_replace('/\D/', '', $candidate);
            if (strlen($clean) >= 7 && substr($clean, -7) === $tail) {
                return true;
            }
        }
        return false;
    }

    private function email_from_text(string $text): string {
        if (preg_match('/[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}/', $text, $match)) {
            return sanitize_email($match[0]);
        }
        return '';
    }

    // Woerter, mit denen ein Satz beginnt - als Beschriftung unbrauchbar.
    private const LABEL_SENTENCE_STARTERS = [
        'wo', 'wie', 'was', 'wann', 'warum', 'wer', 'welche', 'welcher', 'welches', 'ist', 'sind',
        'gibt', 'kann', 'haben', 'habt', 'ich', 'du', 'sie', 'wir', 'moechte', 'möchte', 'how',
        'what', 'where', 'when', 'why', 'who', 'which', 'can', 'do', 'does', 'is', 'are', 'i',
        'you', 'we', 'nasıl', 'nasil', 'nerede', 'hangi', 'kim', 'quel', 'quelle', 'comment',
        'donde', 'dónde', 'como', 'cómo', 'dove', 'quanto',
    ];

    // Fuellwoerter, mit denen ein Label nicht enden darf.
    private const LABEL_TAIL_STOPWORDS = [
        'der', 'die', 'das', 'den', 'dem', 'des', 'ein', 'eine', 'einen', 'einem', 'einer',
        'und', 'oder', 'zu', 'zum', 'zur', 'in', 'im', 'an', 'am', 'auf', 'fuer', 'für', 'mit',
        'von', 'bei', 'sich', 'wie', 'wo', 'was', 'ist', 'sind', 'the', 'a', 'an', 'and', 'or',
        'to', 'for', 'with', 'of', 'on', 'at', 'is', 'are', 'how', 'what', 'where', 'my', 'your',
        've', 'ile', 'için', 'icin', 'bir', 'de', 'la', 'le', 'les', 'des', 'el', 'il', 'et',
    ];

    /** Button-Beschriftung: kurze Menue-Bezeichnung, nie ein Satz oder Fragment. */
    private function clean_action_label(string $value): string {
        $label = trim(preg_replace('/\s+/u', ' ', $value), " \t\n\r\0\x0B\"'");
        $label = trim(preg_replace('/[.!?;:,\-\x{2026}]+$/u', '', $label));
        if ($label === '') {
            return '';
        }
        $words = explode(' ', $label);
        // Ein Satz laesst sich nicht zu einem guten Label kuerzen.
        if (count($words) > 5) {
            return '';
        }
        $first = $this->str_lower(trim($words[0], '¿¡'));
        if (count($words) > 1 && in_array($first, self::LABEL_SENTENCE_STARTERS, true)) {
            return '';
        }
        if (count($words) > 3) {
            $words = array_slice($words, 0, 3);
        }
        while ($words && $this->str_len(implode(' ', $words)) > 24) {
            array_pop($words);
        }
        while (count($words) > 1 && in_array($this->str_lower(trim(end($words), '.,;:!?')), self::LABEL_TAIL_STOPWORDS, true)) {
            array_pop($words);
        }
        if (count($words) === 1 && in_array($this->str_lower($words[0]), self::LABEL_TAIL_STOPWORDS, true)) {
            return '';
        }
        return trim(preg_replace('/[.!?;:,\-]+$/u', '', implode(' ', $words)));
    }

    /** Wortmenge fuer den Aehnlichkeitsvergleich (ohne Fuellwoerter). */
    private function compare_tokens(string $text): array {
        preg_match_all('/[\p{L}\p{N}]{3,}/u', $this->str_lower($text), $found);
        $tokens = array_unique($found[0] ?? []);
        return array_values(array_diff($tokens, self::LABEL_TAIL_STOPWORDS));
    }

    /**
     * True, wenn der Button wiederholt, was schon gefragt oder gezeigt wurde.
     * Gegen die aktuelle Frage wird milder geprueft (viele sinnvolle Folgefragen
     * teilen ein Wort mit ihr), gegen bereits gezeigte Buttons strenger.
     */
    private function is_repeat_action(string $text, array $token_sets, float $ratio = 0.75, int $min_overlap = 2): bool {
        $tokens = $this->compare_tokens($text);
        if (!$tokens) {
            return false;
        }
        foreach ($token_sets as $known) {
            if (!$known) {
                continue;
            }
            $overlap = count(array_intersect($tokens, $known));
            if ($overlap >= $min_overlap && $overlap / min(count($tokens), count($known)) >= $ratio) {
                return true;
            }
        }
        return false;
    }

    private function limit_text(string $text, int $limit): string {
        $clean = trim(preg_replace('/\s+/', ' ', $text));
        if ($this->str_len($clean) <= $limit) {
            return $clean;
        }
        $cut = $this->str_cut($clean, 0, $limit + 1);
        $space = $this->str_rpos($cut, ' ');
        return ($space ? $this->str_cut($cut, 0, $space) : $this->str_cut($clean, 0, $limit)) . '...';
    }

    private function strip_sources_tail(string $answer): string {
        $lines = preg_split('/\n/', $answer) ?: [];
        for ($i = count($lines) - 1; $i >= 0; $i--) {
            if (preg_match('/^\s*(Quellen|Quelle|Sources|Source)\s*:/i', $lines[$i])) {
                return trim(implode("\n", array_slice($lines, 0, $i)));
            }
        }
        return $answer;
    }

    private function decode_json_object(string $raw): ?array {
        $text = trim($raw);
        if ($text === '') {
            return null;
        }
        $data = json_decode($text, true);
        if (is_array($data)) {
            return $data;
        }
        $start = strpos($text, '{');
        $end = strrpos($text, '}');
        if ($start === false || $end === false || $end <= $start) {
            return null;
        }
        $data = json_decode(substr($text, $start, $end - $start + 1), true);
        return is_array($data) ? $data : null;
    }

    private function available_post_types(): array {
        $types = get_post_types(['public' => true], 'objects');
        $items = [];
        foreach ($types as $name => $type) {
            if (in_array($name, ['attachment', 'revision', 'nav_menu_item'], true)) {
                continue;
            }
            $items[] = [
                'name' => $name,
                'label' => $type->label,
                'selected' => in_array($name, $this->enabled_post_type_names(), true),
            ];
        }
        return $items;
    }

    private function enabled_post_type_names(): array {
        $settings = $this->settings();
        $enabled = array_filter(array_map('sanitize_key', (array) ($settings['enabled_post_types'] ?? [])));
        if ($enabled) {
            return $enabled;
        }
        return array_map(fn($item) => $item['name'], $this->available_post_types_without_selection());
    }

    private function available_post_types_without_selection(): array {
        $types = get_post_types(['public' => true], 'objects');
        $items = [];
        foreach ($types as $name => $type) {
            if (!in_array($name, ['attachment', 'revision', 'nav_menu_item'], true)) {
                $items[] = ['name' => $name, 'label' => $type->label];
            }
        }
        return $items;
    }

    private function settings(): array {
        return wp_parse_args((array) get_option(self::OPTION_KEY, []), self::default_settings());
    }

    private function setting(string $key, mixed $default = null): mixed {
        $settings = $this->settings();
        return $settings[$key] ?? $default;
    }

    private function setting_bool(string $key, bool $default): bool {
        return rest_sanitize_boolean($this->setting($key, $default));
    }

    private function settings_for_admin(): array {
        global $wpdb;
        $settings = $this->settings();
        $settings['openai_api_key'] = '';
        $settings['has_openai_api_key'] = trim((string) $this->setting('openai_api_key', '')) !== '';
        $settings['post_types'] = $this->available_post_types();
        $settings['index_count'] = (int) $wpdb->get_var("SELECT COUNT(*) FROM {$wpdb->prefix}aicb_chunks");
        return $settings;
    }

    private function sanitize_setting_value(string $key, mixed $value): mixed {
        return match ($key) {
            'retriever_k', 'max_context_chars', 'batch_size' => absint($value),
            'auto_index_on_save', 'widget_enabled', 'include_excerpts', 'include_taxonomies' => rest_sanitize_boolean($value),
            'enabled_post_types' => array_values(array_filter(array_map('sanitize_key', (array) $value))),
            'privacy_url', 'contact_url' => esc_url_raw((string) $value),
            'contact_email' => sanitize_email((string) $value),
            'system_prompt' => sanitize_textarea_field((string) $value),
            default => sanitize_text_field((string) $value),
        };
    }

    private function public_widget_config(): array {
        $config = $this->sanitize_widget_config((array) get_option(self::WIDGET_OPTION_KEY, self::default_widget_config()));
        $settings = $this->settings();
        $lang = $this->site_lang();
        $pack = $this->lang_pack($lang);

        // Im Admin gesetzte Texte gewinnen, leere Felder kommen aus dem Sprachpaket.
        foreach (['title', 'status', 'intro', 'topics_label', 'placeholder', 'disclaimer', 'privacy_label'] as $key) {
            if (trim((string) ($config['copy'][$key] ?? '')) === '') {
                $config['copy'][$key] = $pack[$key];
            }
        }
        if (trim((string) ($config['greeting']['text'] ?? '')) === '') {
            $config['greeting']['text'] = $pack['greeting'];
        }

        $config['lang'] = $lang;
        $config['rtl'] = $this->is_rtl_lang($lang);
        // Systemtexte des Widgets (Tipp-Indikator, Fehler, Labels) in Seitensprache.
        $config['strings'] = [
            'steps' => $pack['steps'],
            'error' => $pack['error'],
            'sources' => $pack['sources'],
            'sources_labels' => $this->sources_labels(),
            'feedback' => $this->feedback_labels($lang),
        ];
        $config['contact'] = [
            'url' => esc_url_raw((string) ($settings['contact_url'] ?? '')),
            'email' => sanitize_email((string) ($settings['contact_email'] ?? '')),
            'phone' => sanitize_text_field((string) ($settings['contact_phone'] ?? '')),
            'privacy_url' => esc_url_raw((string) ($settings['privacy_url'] ?? '')),
        ];
        return $config;
    }

    private function sanitize_widget_config(array $raw): array {
        $defaults = self::default_widget_config();
        $theme = [];
        foreach (($defaults['theme'] ?? []) as $key => $default) {
            $theme[$key] = sanitize_hex_color((string) ($raw['theme'][$key] ?? $default)) ?: $default;
        }
        $copy = [];
        foreach (($defaults['copy'] ?? []) as $key => $default) {
            $value = (string) ($raw['copy'][$key] ?? $default);
            $copy[$key] = $key === 'icon' ? $this->sanitize_icon($value) : sanitize_text_field($value);
        }
        $greeting = [
            'enabled' => rest_sanitize_boolean($raw['greeting']['enabled'] ?? $defaults['greeting']['enabled']),
            'text' => sanitize_text_field((string) ($raw['greeting']['text'] ?? $defaults['greeting']['text'])),
            'delay_ms' => max(0, absint($raw['greeting']['delay_ms'] ?? $defaults['greeting']['delay_ms'])),
        ];
        $topics = [];
        foreach ((array) ($raw['topics'] ?? $defaults['topics']) as $item) {
            $label = sanitize_text_field((string) ($item['label'] ?? ''));
            $question = sanitize_text_field((string) ($item['question'] ?? ''));
            $url = esc_url_raw((string) ($item['url'] ?? ''));
            // Ein Thema braucht entweder eine Frage oder eine Ziel-URL.
            if ($label !== '' && ($question !== '' || $url !== '')) {
                $topics[] = [
                    'label' => $label,
                    'question' => $question,
                    'url' => $url,
                    'highlight' => rest_sanitize_boolean($item['highlight'] ?? false),
                ];
            }
        }
        return ['theme' => $theme, 'copy' => $copy, 'greeting' => $greeting, 'topics' => $topics];
    }

    private function faqs(): array {
        return (array) get_option(self::FAQ_OPTION_KEY, []);
    }

    /**
     * HTML zu Text, aber mit Struktur. wp_strip_all_tags alleine macht aus
     * Tabellen und Listen einen Fliesstext-Brei - genau dort stehen aber die
     * Details: Preise, Zeiten, Leistungen, Bedingungen.
     */
    private function clean_text(string $html): string {
        $html = (string) $html;
        $html = preg_replace('#<(script|style|noscript|template)[^>]*>.*?</\1>#is', ' ', $html);

        // Ueberschriften als Markdown, damit die Abschnittslogik sie erkennt.
        for ($level = 1; $level <= 6; $level++) {
            $html = preg_replace('#<h' . $level . '[^>]*>(.*?)</h' . $level . '>#is', "\n\n" . str_repeat('#', $level) . ' $1' . "\n", $html);
        }
        // Listenpunkte behalten ihren Aufzaehlungscharakter.
        $html = preg_replace('#<li[^>]*>#i', "\n- ", $html);
        $html = preg_replace('#</li>#i', "\n", $html);
        // Tabellen: Zellen mit | trennen, Zeilen umbrechen.
        $html = preg_replace('#</t[dh]>\s*<t[dh][^>]*>#i', ' | ', $html);
        $html = preg_replace('#<t[dh][^>]*>#i', '', $html);
        $html = preg_replace('#</t[dh]>#i', '', $html);
        $html = preg_replace('#</tr>#i', "\n", $html);
        $html = preg_replace('#</(caption|table)>#i', "\n\n", $html);
        // Definitionslisten und Absaetze.
        $html = preg_replace('#<dt[^>]*>#i', "\n- ", $html);
        $html = preg_replace('#</dt>#i', ': ', $html);
        $html = preg_replace('#<br\s*/?>#i', "\n", $html);
        $html = preg_replace('#</(p|div|section|article|tr|dd|blockquote|figcaption)>#i', "\n\n", $html);

        $text = wp_strip_all_tags($html, false);
        $text = $this->normalize_text($text);
        // Aufzaehlungen sauber halten: keine leeren Punkte, keine Doppelstriche.
        $text = preg_replace('/\n-\s*\n/', "\n", $text);
        $text = preg_replace('/^-\s*$/m', '', $text);
        $text = preg_replace('/\n{2,}(?=- )/', "\n", $text);
        return trim(preg_replace('/\n{3,}/', "\n\n", $text));
    }

    private function normalize_text(string $text): string {
        $text = html_entity_decode($text, ENT_QUOTES | ENT_HTML5, get_bloginfo('charset') ?: 'UTF-8');
        $text = str_replace(["\r\n", "\r", "\xc2\xa0"], ["\n", "\n", ' '], $text);
        $text = preg_replace('/[ \t]+\n/', "\n", $text);
        $text = preg_replace('/\n[ \t]+/', "\n", $text);
        $text = preg_replace('/[ \t]{2,}/', ' ', $text);
        $text = preg_replace('/\n{3,}/', "\n\n", $text);
        return trim((string) $text);
    }

    private function estimate_tokens(string $text): int {
        $len = strlen(trim($text));
        return $len > 0 ? max(1, (int) ceil($len / 4)) : 0;
    }

    private function estimate_cost(int $input, int $output): float {
        $model = (string) $this->setting('chat_model', 'gpt-4o-mini');
        $pricing = [
            'gpt-4o-mini' => ['input' => 0.000150, 'output' => 0.000600],
            'gpt-4o' => ['input' => 0.0025, 'output' => 0.0100],
        ];
        $p = $pricing[$model] ?? $pricing['gpt-4o-mini'];
        return round(($input / 1000) * $p['input'] + ($output / 1000) * $p['output'], 4);
    }

    private function cosine_similarity(array $a, array $b): float {
        $dot = 0.0;
        $norm_a = 0.0;
        $norm_b = 0.0;
        $n = min(count($a), count($b));
        for ($i = 0; $i < $n; $i++) {
            $av = (float) $a[$i];
            $bv = (float) $b[$i];
            $dot += $av * $bv;
            $norm_a += $av * $av;
            $norm_b += $bv * $bv;
        }
        if ($norm_a <= 0 || $norm_b <= 0) {
            return 0.0;
        }
        return $dot / (sqrt($norm_a) * sqrt($norm_b));
    }

    private function clear_chunks(): void {
        global $wpdb;
        $wpdb->query("TRUNCATE TABLE {$wpdb->prefix}aicb_chunks");
    }

    private function delete_source_chunks(string $source_id): void {
        global $wpdb;
        $wpdb->delete($wpdb->prefix . 'aicb_chunks', ['source_id' => $source_id], ['%s']);
    }

    private function delete_source_chunks_by_type(string $type): void {
        global $wpdb;
        $wpdb->delete($wpdb->prefix . 'aicb_chunks', ['source_type' => $type], ['%s']);
    }

    private function job_key(string $job_id): string {
        return 'aicb_train_' . sanitize_key($job_id);
    }

    private function public_job(array $job): array {
        $copy = $job;
        unset($copy['ids'], $copy['queue']);
        return $copy;
    }

    private function hash_token(string $token): string {
        return hash('sha256', $token . wp_salt('auth'));
    }

    private function request_ip_hash(): string {
        $ip = (string) ($_SERVER['REMOTE_ADDR'] ?? '');
        return hash('sha256', $ip . wp_salt('nonce'));
    }
}

register_activation_hook(__FILE__, ['AICB_Plugin', 'activate']);
register_deactivation_hook(__FILE__, ['AICB_Plugin', 'deactivate']);
AICB_Plugin::instance();
