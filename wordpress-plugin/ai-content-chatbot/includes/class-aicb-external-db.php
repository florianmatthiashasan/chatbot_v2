<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * Small wpdb-compatible adapter for the plugin's dedicated MySQL database.
 *
 * It uses mysqli because every normal WordPress installation already provides
 * that extension. All AICB-owned records stay outside WordPress' global $wpdb.
 */
final class AICB_External_DB {
    public string $prefix;
    public int $insert_id = 0;
    public string $last_error = '';

    private mysqli $connection;
    private bool $suppress_errors = false;

    public function __construct(array $config) {
        if (!extension_loaded('mysqli')) {
            throw new RuntimeException('AICB benoetigt die PHP-Erweiterung mysqli.');
        }

        $host = trim((string) ($config['host'] ?? ''));
        $port = (int) ($config['port'] ?? 3306);
        $name = trim((string) ($config['name'] ?? ''));
        $user = (string) ($config['user'] ?? '');
        $password = (string) ($config['password'] ?? '');
        $ssl_ca = trim((string) ($config['ssl_ca'] ?? ''));

        if ($host === '' || $name === '' || $user === '' || $password === '') {
            throw new RuntimeException('AICB-Datenbankkonfiguration ist unvollstaendig.');
        }
        if ($ssl_ca === '' || !is_readable($ssl_ca)) {
            throw new RuntimeException('AICB_DB_SSL_CA fehlt oder ist nicht lesbar.');
        }

        $raw_prefix = (string) ($config['prefix'] ?? '');
        if ($raw_prefix !== '' && !preg_match('/^[A-Za-z0-9_]+$/', $raw_prefix)) {
            throw new RuntimeException('AICB_DB_PREFIX darf nur Buchstaben, Zahlen und Unterstriche enthalten.');
        }
        $this->prefix = $raw_prefix;

        mysqli_report(MYSQLI_REPORT_OFF);
        $connection = mysqli_init();
        if (!$connection) {
            throw new RuntimeException('AICB konnte mysqli nicht initialisieren.');
        }
        mysqli_options($connection, MYSQLI_OPT_CONNECT_TIMEOUT, 8);
        if (defined('MYSQLI_OPT_SSL_VERIFY_SERVER_CERT')) {
            mysqli_options($connection, MYSQLI_OPT_SSL_VERIFY_SERVER_CERT, true);
        }
        if (!mysqli_ssl_set($connection, null, null, $ssl_ca, null, null)) {
            throw new RuntimeException('AICB konnte die TLS-Konfiguration nicht setzen.');
        }

        $connected = @mysqli_real_connect(
            $connection,
            $host,
            $user,
            $password,
            $name,
            $port > 0 ? $port : 3306,
            null,
            MYSQLI_CLIENT_SSL
        );
        if (!$connected) {
            $message = mysqli_connect_error() ?: 'Unbekannter Verbindungsfehler.';
            throw new RuntimeException('Verbindung zur AICB-Datenbank fehlgeschlagen: ' . $message);
        }
        if (!mysqli_set_charset($connection, 'utf8mb4')) {
            throw new RuntimeException('AICB konnte den Zeichensatz utf8mb4 nicht setzen.');
        }
        $this->connection = $connection;
    }

    public function get_charset_collate(): string {
        return 'DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci';
    }

    public function suppress_errors(?bool $suppress = null): bool {
        $previous = $this->suppress_errors;
        if ($suppress !== null) {
            $this->suppress_errors = $suppress;
        }
        return $previous;
    }

    public function esc_like(string $text): string {
        return addcslashes($text, '_%\\');
    }

    public function prepare(string $query, mixed ...$args): string {
        if (count($args) === 1 && is_array($args[0])) {
            $args = $args[0];
        }
        $index = 0;
        $prepared = preg_replace_callback('/%%|%[dfs]/', function (array $match) use (&$args, &$index): string {
            if ($match[0] === '%%') {
                return '%';
            }
            if (!array_key_exists($index, $args)) {
                throw new InvalidArgumentException('Zu wenige Werte fuer AICB_External_DB::prepare().');
            }
            $value = $args[$index++];
            return match ($match[0]) {
                '%d' => (string) (int) $value,
                '%f' => (string) (float) $value,
                default => $this->sql_literal((string) $value),
            };
        }, $query);
        if ($prepared === null || $index !== count($args)) {
            throw new InvalidArgumentException('Ungueltige Platzhalter in AICB_External_DB::prepare().');
        }
        return $prepared;
    }

    public function query(string $query): int|false {
        $result = @mysqli_query($this->connection, $query);
        if ($result === false) {
            return $this->handle_error();
        }
        $this->last_error = '';
        if ($result instanceof mysqli_result) {
            $count = $result->num_rows;
            $result->free();
            return $count;
        }
        return mysqli_affected_rows($this->connection);
    }

    public function get_var(string $query, int $column = 0, int $row = 0): mixed {
        $rows = $this->fetch_rows($query, MYSQLI_NUM);
        return $rows[$row][$column] ?? null;
    }

    public function get_row(string $query, string $output = OBJECT, int $row = 0): array|object|null {
        $rows = $this->get_results($query, $output);
        return $rows[$row] ?? null;
    }

    public function get_results(string $query, string $output = OBJECT): array {
        if ($output === ARRAY_A) {
            return $this->fetch_rows($query, MYSQLI_ASSOC);
        }
        if ($output === ARRAY_N) {
            return $this->fetch_rows($query, MYSQLI_NUM);
        }
        $rows = $this->fetch_rows($query, MYSQLI_ASSOC);
        return array_map(static fn(array $row): object => (object) $row, $rows);
    }

    public function insert(string $table, array $data, ?array $format = null): int|false {
        if (!$data) {
            return false;
        }
        $columns = array_keys($data);
        $sql = 'INSERT INTO ' . $this->quote_identifier($table)
            . ' (' . implode(', ', array_map([$this, 'quote_identifier'], $columns)) . ')'
            . ' VALUES (' . implode(', ', array_map([$this, 'sql_literal'], array_values($data))) . ')';
        $result = $this->query($sql);
        if ($result !== false) {
            $this->insert_id = (int) mysqli_insert_id($this->connection);
        }
        return $result;
    }

    public function update(string $table, array $data, array $where, ?array $format = null, ?array $where_format = null): int|false {
        if (!$data || !$where) {
            return false;
        }
        $set = [];
        foreach ($data as $column => $value) {
            $set[] = $this->quote_identifier((string) $column) . ' = ' . $this->sql_literal($value);
        }
        $conditions = $this->where_conditions($where);
        return $this->query(
            'UPDATE ' . $this->quote_identifier($table)
            . ' SET ' . implode(', ', $set)
            . ' WHERE ' . implode(' AND ', $conditions)
        );
    }

    public function delete(string $table, array $where, ?array $where_format = null): int|false {
        if (!$where) {
            return false;
        }
        return $this->query(
            'DELETE FROM ' . $this->quote_identifier($table)
            . ' WHERE ' . implode(' AND ', $this->where_conditions($where))
        );
    }

    public function replace(string $table, array $data): int|false {
        if (!$data) {
            return false;
        }
        $columns = array_keys($data);
        $sql = 'REPLACE INTO ' . $this->quote_identifier($table)
            . ' (' . implode(', ', array_map([$this, 'quote_identifier'], $columns)) . ')'
            . ' VALUES (' . implode(', ', array_map([$this, 'sql_literal'], array_values($data))) . ')';
        $result = $this->query($sql);
        if ($result !== false) {
            $this->insert_id = (int) mysqli_insert_id($this->connection);
        }
        return $result;
    }

    private function fetch_rows(string $query, int $mode): array {
        $result = @mysqli_query($this->connection, $query);
        if ($result === false) {
            $this->handle_error();
            return [];
        }
        if (!($result instanceof mysqli_result)) {
            return [];
        }
        $rows = mysqli_fetch_all($result, $mode);
        $result->free();
        $this->last_error = '';
        return $rows;
    }

    private function where_conditions(array $where): array {
        $conditions = [];
        foreach ($where as $column => $value) {
            $identifier = $this->quote_identifier((string) $column);
            $conditions[] = $value === null
                ? $identifier . ' IS NULL'
                : $identifier . ' = ' . $this->sql_literal($value);
        }
        return $conditions;
    }

    private function sql_literal(mixed $value): string {
        if ($value === null) {
            return 'NULL';
        }
        if (is_int($value) || is_float($value)) {
            return (string) $value;
        }
        if (is_bool($value)) {
            return $value ? '1' : '0';
        }
        return "'" . mysqli_real_escape_string($this->connection, (string) $value) . "'";
    }

    private function quote_identifier(string $identifier): string {
        if (!preg_match('/^[A-Za-z0-9_]+$/', $identifier)) {
            throw new InvalidArgumentException('Ungueltiger SQL-Bezeichner.');
        }
        return '`' . $identifier . '`';
    }

    private function handle_error(): bool {
        $this->last_error = mysqli_error($this->connection);
        if (!$this->suppress_errors) {
            throw new RuntimeException('AICB-Datenbankfehler: ' . $this->last_error);
        }
        return false;
    }
}
