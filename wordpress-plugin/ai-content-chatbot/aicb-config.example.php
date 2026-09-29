<?php
/**
 * Copy these definitions into wp-config.php before the line
 * "That's all, stop editing!". Do not put the password in the plugin itself.
 */
define('AICB_DB_HOST', 'mysql-171fcc14-florianmatthias-c01c.a.aivencloud.com');
define('AICB_DB_PORT', 16916);
define('AICB_DB_NAME', 'defaultdb');
define('AICB_DB_USER', 'avnadmin');
define('AICB_DB_PASSWORD', 'PASTE_THE_AIVEN_PASSWORD_HERE');

// Optional: the plugin uses its bundled Aiven CA certificate by default.
// define('AICB_DB_SSL_CA', __DIR__ . '/wp-content/plugins/ai-content-chatbot/certs/aiven-ca.pem');

// Optional override. Without this line a stable prefix is generated automatically
// from the WordPress domain and URL hash, keeping every website isolated.
// define('AICB_DB_PREFIX', 'mywebsite_');
