=== AI Content Chatbot ===
Contributors: local
Tags: chatbot, ai, openai, rag, custom post types
Requires at least: 6.2
Requires PHP: 8.0
Stable tag: 0.3.4
License: GPLv2 or later

Standalone WordPress chatbot that trains from published pages, posts and public custom post types.

== Description ==

AI Content Chatbot adds a floating chat widget and an admin dashboard directly inside WordPress. It does not crawl a sitemap. Training reads the selected public post types, chunks the content, creates OpenAI embeddings, stores them in local WordPress database tables, and answers questions using retrieval augmented generation.

Main features:

* Training from pages, posts and public custom post types
* Automatic session token creation and renewal for visitors
* Server-side OpenAI API calls only
* Local WordPress tables for chunks, sessions and chat events
* Admin settings for models, prompt, post types and contact details
* Widget theme and quick-topic editor with live preview of the real chat window
* FAQ manager
* Memory search, edit and delete
* Chat statistics and estimated cost overview
* Optional automatic reindexing when a published post is saved
* Shortcode: [ai_content_chatbot]
* Chat-Oberflaeche im florianmatthias-Widget-Design: Startansicht mit Themenliste, Antwortkarten mit Bild, Quellenblock und Anker-Scrolling
* Antwort-Buttons werden von der KI aus dem Gespraech erzeugt (Anrufen, Kontaktseite, Folgefragen)
* Anruf- und Mail-Buttons nutzen nur Nummern und Adressen, die in den indexierten Inhalten oder den Einstellungen stehen
* Fortlaufendes Gespraech: Begruessungen und Small Talk werden normal beantwortet, Folgefragen beziehen sich auf den Verlauf
* Mehrsprachig: Oberflaeche in der Sprache der Website, Antworten immer in der Sprache der Frage - inklusive Rechts-nach-links-Layout

== Installation ==

1. Upload the ai-content-chatbot folder to wp-content/plugins/.
2. Activate the plugin in WordPress.
3. Open AI Chatbot in the admin menu.
4. Save an OpenAI API key.
5. Select post types and start training.

== Notes ==

The plugin stores embeddings in the WordPress database as JSON vectors. This keeps the plugin standalone and easy to install. For very large sites, a dedicated vector database can be added later.


== Changelog ==

= 0.3.4 =
* Button-Beschriftungen sind jetzt kurze Menue-Texte aus zwei bis drei Woertern. Saetze und abgeschnittene Wortgruppen wie "Wo befindet sich die" werden verworfen statt gekuerzt.
* Kein Button wiederholt mehr eine schon gestellte Frage oder eine Empfehlung, die im selben Gespraech bereits angezeigt wurde - das Widget meldet dem Server, was er schon gezeigt hat.
* Fix: Aktions-Buttons gingen verloren, wenn das Modell "type": "phone" statt "type": "link" mit "target": "phone" schrieb.

= 0.3.3 =
* Antwortkarten passen jetzt zur Antwort: aus den Treffern werden bis zu drei Kandidaten gebildet, das Modell waehlt die passende Seite oder ausdruecklich keine. Vorher war es immer der beste Suchtreffer, auch wenn er nichts mit der Antwort zu tun hatte.
* Rechtstexte, Startseite, Archive, Login-, Konto- und Shop-Seiten kommen als Karte nicht mehr in Frage.
* Ohne Titel oder ohne Ziel-URL wird keine Karte mehr gebaut.

= 0.3.2 =
* Eigenes Logo-SVG als Standard-Icon statt Emoji; zeichnet sich in der Avatar-Farbe und laesst sich im Widget-Tab durch ein anderes SVG oder ein Emoji ersetzen.
* Logo erscheint groesser in Chat-Button, Kopfzeile und Verlauf (32/24/21 px).
* Fix: Ein eigenes SVG im Icon-Feld wurde beim Speichern von sanitize_text_field komplett entfernt. SVGs laufen jetzt ueber eine Tag-Freigabe, Emojis und Text weiterhin ueber die Standard-Bereinigung.
* Icon-Feld im Admin ist ein mehrzeiliges Feld, damit ein SVG hineinpasst.

= 0.3.1 =
* Antwortsprache wird jetzt deterministisch aus der Nutzernachricht bestimmt (Schriftsystem plus Funktionswoerter) und dem Modell fest vorgegeben. Vorher konnte ein deutscher Verlauf dazu fuehren, dass eine englische Frage deutsch beantwortet wurde.
* Antwort-Buttons und Quellenblock folgen derselben erkannten Sprache und nicht mehr dem, was das Modell aus aelteren Nachrichten herausliest.

= 0.2.0 =
* Widget-Design komplett ueberarbeitet: Kopfzeile mit Avatar und Statuszeile, Themenliste in der Startansicht, runde Karten und Aktionsbuttons, Composer mit Sende-Pfeil, Fusszeile mit Datenschutz-Link.
* Antworten unterstuetzen Markdown (fett, kursiv, Code, Links) und trennen den Quellenblock ab.
* Antwort-Buttons werden von der KI aus dem Gespraech erzeugt; die statischen Texte greifen nur noch als Fallback.
* Anruf-Buttons (tel:) und Mail-Buttons (mailto:) nur mit belegten Nummern und Adressen.
* Antwortkarte mit Beitragsbild, Teaser und Link zum Treffer.
* Begruessungs-Popup neben dem Chat-Button.
* Fix: Das Panel liess sich nicht schliessen, weil display:grid das hidden-Attribut ueberschrieb.

= 0.3.0 =
* Live-Vorschau im Widget-Tab: das echte Chat-Fenster neben dem Formular, jede Aenderung sofort sichtbar, Beispielantwort auf Knopfdruck.
* Farbfelder mit lesbaren Bezeichnungen statt interner Schluesselnamen.
* Fortlaufendes Gespraech: Begruessung, Dank und Small Talk werden freundlich beantwortet statt mit "keine Informationen gefunden".
* Der Verlauf geht als echte Chat-Nachrichten an das Modell, Folgefragen wie "und die Suiten?" funktionieren.
* Kurze Folgefragen werden fuer die Suche eigenstaendig formuliert; gesucht wird mit Original und Umformulierung.
* Antwortsprache folgt der Nutzerfrage (Tuerkisch, Arabisch, Franzoesisch ... auch auf einer deutschen Website).
* Oberflaechentexte in elf Sprachen; leere Felder werden automatisch in der Sprache der Website gefuellt.
* Rechts-nach-links-Layout fuer Arabisch und Hebraeisch, einzelne Antworten richten sich per dir=auto selbst aus.
* Karte und Quellenblock erscheinen nur bei inhaltlichen Antworten, nicht bei Small Talk.
* Quellenblock und Antwort-Buttons in der Sprache der Antwort.
* Ohne Index wird nicht mehr abgebrochen: der Bot sagt freundlich, dass er dazu noch keine Informationen hat.

= 0.1.0 =
* Erste Version.
