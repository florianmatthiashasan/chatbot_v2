=== AI Content Chatbot ===
Contributors: local
Tags: chatbot, ai, openai, rag, custom post types
Requires at least: 6.2
Requires PHP: 8.0
Stable tag: 0.8.2
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
* Chat statistics and estimated cost overview - inklusive Feedback-Auswertung (👍/👎, Zufriedenheit)
* Feedback-Leiste "War das hilfreich?" unter jeder Antwort; die Auswertung erscheint sauber im Statistik-Tab
* Optional automatic reindexing when a published post is saved
* Inhalte-Tab: einzelne veröffentlichte Beiträge/Seiten per Häkchen zum Indexieren auswählen (Entwürfe werden nie indexiert)
* PDFs aus der Mediathek auswählen und in die Wissensbasis aufnehmen - robuste Textextraktion über die gebündelte Bibliothek Smalot/PdfParser (CID/Type0, ToUnicode, Differences, CFF, Positionierung); gescannte Bild-PDFs ohne Textebene werden übersprungen
* Shortcode: [ai_content_chatbot]
* Chat-Oberfläche im florianmatthias-Widget-Design: Startansicht mit Themenliste, Antwortkarten mit Bild, Quellenblock und Anker-Scrolling
* Antwort-Buttons werden von der KI aus dem Gespräch erzeugt (Anrufen, Kontaktseite, Folgefragen)
* Anruf- und Mail-Buttons nutzen nur Nummern und Adressen, die in den indexierten Inhalten oder den Einstellungen stehen
* Fortlaufendes Gespräch: Begrüßungen und Small Talk werden normal beantwortet, Folgefragen beziehen sich auf den Verlauf
* Mehrsprachig: Oberfläche in der Sprache der Website, Antworten immer in der Sprache der Frage - inklusive Rechts-nach-links-Layout

== Installation ==

1. Upload the ai-content-chatbot folder to wp-content/plugins/.
2. Activate the plugin in WordPress.
3. Open AI Chatbot in the admin menu.
4. Save an OpenAI API key.
5. Select post types and start training.

== Notes ==

The plugin stores embeddings in the WordPress database as JSON vectors. This keeps the plugin standalone and easy to install. For very large sites, a dedicated vector database can be added later.

Die PDF-Textextraktion nutzt die mitgelieferte Bibliothek Smalot/PdfParser (MIT-Lizenz) samt symfony/polyfill-mbstring (MIT). Beide liegen unter vendor/ und benötigen kein Composer beim Nutzer.


== Changelog ==

= 0.8.2 =
* Schnellere Suche bei identischer Qualitaet: Embeddings werden jetzt kompakt gepackt gespeichert (Base64 float32, ca. 2,4x kleiner als vorher) und normalisiert - die Aehnlichkeitssuche wird zum schnellen Skalarprodukt statt JSON zu parsen. Ergebnisse sind bit-genau dieselben (verifiziert). Alte Eintraege laufen unveraendert weiter; fuer den vollen Tempogewinn einmal neu trainieren.
* Mehr Wissen pro Antwort: retriever_k 12 -> 16 und Kontext 20000 -> 26000 Zeichen angehoben (nur alte Standardwerte, eigene Einstellungen bleiben).

= 0.8.1 =
* Eigene SVG-Logos passen jetzt IMMER in den Kreis - unabhaengig von der im Code angegebenen Groesse. Neuer DOM-basierter SVG-Sanitizer ersetzt wp_kses: width/height werden entfernt (CSS bestimmt die Groesse, preserveAspectRatio gesetzt), und komplexe SVGs mit Verlaeufen/Filtern (linearGradient, feDropShadow, viewBox, gradientUnits ...) bleiben erhalten, weil ihre Gross-/Kleinschreibung nicht mehr zerstoert wird. Schwarz/fehlende Fuellung weiterhin -> Theme-Farbe; Script/onload/externe Verweise werden entfernt.

= 0.8.0 =
* Statistik-Seite komplett neu als modernes Dashboard: KPI-Karten (heute/7/30 Tage/gesamt, Antwortquote, Zufriedenheit, Sessions, Ø Nachrichten je Session, Wissens-Chunks) plus Diagramme via gebuendeltem Chart.js (kein CDN) - Chats pro Tag (30 Tage, inkl. beantwortet), Antwortquote-Donut, Feedback-Donut, Verteilung nach Wochentag und Tageszeit sowie Top-Fragen als Balken. Preis/Kosten entfernt.

= 0.7.9 =
* Fragen-Popup (Greeting-Stil) zuverlaessiger: erscheint jetzt einmal pro Sitzung je Seite, auch wenn das klassische Greeting deaktiviert ist (Greeting nur noch Fallback).
* Avatar-Feinschliff: Header-Avatar zurueck auf Normalgroesse; der Avatar in den Chat-Nachrichten etwas kleiner.

= 0.7.8 =
* Icon-Feinschliff: Launcher-Icon (vor dem Oeffnen) etwas groesser und mittig; Avatar-Icon im geoeffneten Chat-Header etwas kleiner.

= 0.7.7 =
* Icon-Fix: Eigene SVG-Logos werden zuverlässig in der Widget-Farbe dargestellt (nie mehr durchgehend schwarz) - Schwarz und fehlende Füllungen werden auf currentColor gesetzt, Umrisse bleiben Umrisse, echte Markenfarben bleiben erhalten. Icon im Chat-Kreis größer und sauber zentriert.
* KI-Fragen jetzt auch in der Themenliste beim Öffnen des Chats (anklickbar), nicht nur im Popup - dezent hervorgehoben.
* KI-Fragen aktualisieren sich bei Seitenwechsel ohne Full-Reload (SPA-/AJAX-Themes, Page-Builder): History-API-Erkennung plus Fallback, damit die Fragen zur neuen Seite passen.
* Fragen-Popup mit kurzer Lead-Zeile; Admin-Live-Vorschau ruft die Fragen-API nicht mehr auf (spart OpenAI-Aufrufe).

= 0.7.6 =
* Page-spezifische KI-Fragen: Das Widget erzeugt pro aktueller Unterseite 2-3 passende Einstiegsfragen und zeigt sie im Greeting-Popup-Stil. Ein Klick öffnet den Chat und sendet die Frage direkt.

= 0.7.5 =
* SVGRepo- und andere Custom-SVGs mit schwarzem `fill`/`stroke` werden für Widget-Icons automatisch auf die konfigurierbare Icon-Farbe umgebogen.

= 0.7.4 =
* Custom-SVG-Icons mit `currentColor` erben ihre Farbe jetzt zuverlässiger aus dem Widget.

= 0.7.3 =
* Standard-Icon und Avatar-Darstellung verfeinert: heller Profil-Kreis, bessere Sichtbarkeit im geöffneten Chat und aktualisierte gespeicherte Standardfarben.
* Greeting-Popup und Icon-Rendering im Widget robuster gemacht.

= 0.7.1 =
* Durchgängig korrekte deutsche Umlaute und ß in allen sichtbaren Texten (Admin-Oberfläche, Widget, Meldungen) statt ue/oe/ae/ss.

= 0.7.0 =
* PDF-Extraktion grundlegend robuster: Das Plugin bringt jetzt die Bibliothek Smalot/PdfParser mit (reines PHP, kein Composer nötig). Damit werden auch anspruchsvolle PDFs korrekt gelesen - CID-/Type0-Fonts, Type1/CFF-Subsets, ToUnicode und /Differences, inklusive korrekter Wort- und Zeilentrennung. Getestet an realen Dateien (mehrseitige Weinkarte mit Preisen, Projekt-Statusdokument), die zuvor gar nicht oder nur als Zeichensalat erkannt wurden - jetzt sauber im Index.
* Extraktions-Reihenfolge: Smalot (immer verfügbar) -> pdftotext (falls auf dem Host vorhanden) -> eingebaute PHP-Extraktion. Danach greift weiterhin die Qualitätsprüfung.
* Hinweis: Nach dem Update die PDFs im Inhalte-Tab (falls noch nicht geschehen) auswählen und einmal neu trainieren.

= 0.6.1 =
* PDF-Extraktion deutlich verbessert: Fonts mit /Encoding /Differences (Glyphnamen) werden jetzt korrekt aufgelöst - genau die Subset-Fonts aus Word/LibreOffice/InDesign, die zuvor als Zeichensalat verworfen wurden, liefern nun sauberen Text (inkl. Umlauten und korrekter Worttrennung über Kerning).
* Falls auf dem Server "pdftotext" (poppler) verfügbar ist, wird es bevorzugt genutzt (beste Qualität); sonst greift die reine PHP-Extraktion.
* Die Qualitätsprüfung bleibt aktiv: echte Bild-/Scan-PDFs ohne Textebene und weiterhin unlesbare Fonts (z. B. CID ohne ToUnicode) werden weiterhin sauber übersprungen, statt Müll zu indexieren.

= 0.6.0 =
* Feedback-Leiste "War das hilfreich?" mit 👍/👎 unter jeder Antwort im Widget (mehrsprachig, einmalige Abstimmung pro Antwort).
* Die Bewertung wird der jeweiligen Antwort zugeordnet und nur vom Besitzer der Session gespeichert (Missbrauchsschutz über den Session-Token).
* Statistik-Tab zeigt die Auswertung sauber: 👍 hilfreich, 👎 nicht hilfreich, Zufriedenheit (Anteil positiv), Gesamtzahl der Bewertungen sowie die letzten 30 Tage.
* DB: neue Spalte `feedback` in der Events-Tabelle (automatische Migration bestehender Installationen).

= 0.5.0 =
* Neuer Tab "Inhalte": Statt nur ganzer Post-Types lassen sich jetzt einzelne veröffentlichte Beiträge und Seiten per Häkchen für das Training auswählen. Zwei Modi - "Alle veröffentlichten Inhalte der aktivierten Post Types" (bisheriges Verhalten) oder "Nur die angekreuzten Inhalte". Entwürfe werden in keinem Modus indexiert.
* PDFs aus der Mediathek: Über den WordPress-Medien-Dialog lassen sich PDFs auswählen; ihr Text wird beim Training extrahiert, in Abschnitte zerlegt und in die Wissensbasis aufgenommen. Reine PHP-Extraktion (FlateDecode-Streams, literale/hexadezimale Strings, Tj/TJ, ToUnicode-CMaps). Gescannte Bild-PDFs ohne Textebene sowie unlesbare (Subset-Font-)PDFs werden erkannt und übersprungen, damit kein Zeichensalat in den Index gelangt.
* Auto-Reindex beim Speichern respektiert den Selektiv-Modus: abgewählte Inhalte werden nicht wieder aufgenommen.
* Hinweis: Für die neue Auswahl ggf. einmal neu trainieren.

= 0.4.0 =
* Der Bot kennt jetzt die Details. Bisher wurden Tabellen und Listen beim Indexieren zu einem Wortbrei zusammengezogen ("Doppelzimmer180 EURSuite260 EUR") - Preise, Zeiten und Bedingungen waren praktisch nicht auffindbar. Tabellen werden jetzt als Zeilen mit Spaltentrennung, Listen als Aufzählung und Überschriften als Abschnitte übernommen.
* Kleinere Abschnitte (380 statt 620 Token) mit Überlappung: ein Detail an der Grenze zweier Abschnitte geht nicht mehr verloren.
* Zu jedem Treffer wird der direkt angrenzende Abschnitt derselben Seite mitgeladen - die Tabelle im einen, die Bedingungen im nächsten Abschnitt.
* Mehr Kontext pro Antwort: Standard jetzt 12 Abschnitte und 20.000 Zeichen (vorher 8 und 14.000). Bestehende Installationen werden angehoben, sofern die alten Standardwerte unverändert waren.
* Antwortregel: konkrete Zahlen, Preise, Zeiten und Bedingungen nennen statt vager Zusammenfassung; mehrere Varianten einzeln aufführen.
* WICHTIG: Für die Verbesserungen ist ein neues Training nötig, da sich die Aufbereitung der Inhalte geändert hat.

= 0.3.4 =
* Button-Beschriftungen sind jetzt kurze Menü-Texte aus zwei bis drei Wörtern. Sätze und abgeschnittene Wortgruppen wie "Wo befindet sich die" werden verworfen statt gekürzt.
* Kein Button wiederholt mehr eine schon gestellte Frage oder eine Empfehlung, die im selben Gespräch bereits angezeigt wurde - das Widget meldet dem Server, was er schon gezeigt hat.
* Fix: Aktions-Buttons gingen verloren, wenn das Modell "type": "phone" statt "type": "link" mit "target": "phone" schrieb.

= 0.3.3 =
* Antwortkarten passen jetzt zur Antwort: aus den Treffern werden bis zu drei Kandidaten gebildet, das Modell wählt die passende Seite oder ausdrücklich keine. Vorher war es immer der beste Suchtreffer, auch wenn er nichts mit der Antwort zu tun hatte.
* Rechtstexte, Startseite, Archive, Login-, Konto- und Shop-Seiten kommen als Karte nicht mehr in Frage.
* Ohne Titel oder ohne Ziel-URL wird keine Karte mehr gebaut.

= 0.3.2 =
* Eigenes Logo-SVG als Standard-Icon statt Emoji; zeichnet sich in der Avatar-Farbe und lässt sich im Widget-Tab durch ein anderes SVG oder ein Emoji ersetzen.
* Logo erscheint größer in Chat-Button, Kopfzeile und Verlauf (32/24/21 px).
* Fix: Ein eigenes SVG im Icon-Feld wurde beim Speichern von sanitize_text_field komplett entfernt. SVGs laufen jetzt über eine Tag-Freigabe, Emojis und Text weiterhin über die Standard-Bereinigung.
* Icon-Feld im Admin ist ein mehrzeiliges Feld, damit ein SVG hineinpasst.

= 0.3.1 =
* Antwortsprache wird jetzt deterministisch aus der Nutzernachricht bestimmt (Schriftsystem plus Funktionswörter) und dem Modell fest vorgegeben. Vorher konnte ein deutscher Verlauf dazu führen, dass eine englische Frage deutsch beantwortet wurde.
* Antwort-Buttons und Quellenblock folgen derselben erkannten Sprache und nicht mehr dem, was das Modell aus älteren Nachrichten herausliest.

= 0.2.0 =
* Widget-Design komplett überarbeitet: Kopfzeile mit Avatar und Statuszeile, Themenliste in der Startansicht, runde Karten und Aktionsbuttons, Composer mit Sende-Pfeil, Fußzeile mit Datenschutz-Link.
* Antworten unterstützen Markdown (fett, kursiv, Code, Links) und trennen den Quellenblock ab.
* Antwort-Buttons werden von der KI aus dem Gespräch erzeugt; die statischen Texte greifen nur noch als Fallback.
* Anruf-Buttons (tel:) und Mail-Buttons (mailto:) nur mit belegten Nummern und Adressen.
* Antwortkarte mit Beitragsbild, Teaser und Link zum Treffer.
* Begrüßungs-Popup neben dem Chat-Button.
* Fix: Das Panel ließ sich nicht schließen, weil display:grid das hidden-Attribut überschrieb.

= 0.3.0 =
* Live-Vorschau im Widget-Tab: das echte Chat-Fenster neben dem Formular, jede Änderung sofort sichtbar, Beispielantwort auf Knopfdruck.
* Farbfelder mit lesbaren Bezeichnungen statt interner Schlüsselnamen.
* Fortlaufendes Gespräch: Begrüßung, Dank und Small Talk werden freundlich beantwortet statt mit "keine Informationen gefunden".
* Der Verlauf geht als echte Chat-Nachrichten an das Modell, Folgefragen wie "und die Suiten?" funktionieren.
* Kurze Folgefragen werden für die Suche eigenständig formuliert; gesucht wird mit Original und Umformulierung.
* Antwortsprache folgt der Nutzerfrage (Türkisch, Arabisch, Französisch ... auch auf einer deutschen Website).
* Oberflächentexte in elf Sprachen; leere Felder werden automatisch in der Sprache der Website gefüllt.
* Rechts-nach-links-Layout für Arabisch und Hebräisch, einzelne Antworten richten sich per dir=auto selbst aus.
* Karte und Quellenblock erscheinen nur bei inhaltlichen Antworten, nicht bei Small Talk.
* Quellenblock und Antwort-Buttons in der Sprache der Antwort.
* Ohne Index wird nicht mehr abgebrochen: der Bot sagt freundlich, dass er dazu noch keine Informationen hat.

= 0.1.0 =
* Erste Version.
