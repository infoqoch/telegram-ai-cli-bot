#!/bin/bash

cd "$(dirname "$0")"

DB_PATH="$(pwd)/.data/bot.db"
CODEX_STATE_DB="${CODEX_STATE_DB:-$HOME/.codex/state_5.sqlite}"
SELECTED_TABLE=""

require_sqlite3() {
    if ! command -v sqlite3 > /dev/null 2>&1; then
        echo "sqlite3 command not found."
        exit 1
    fi
}

require_db_file() {
    if [ ! -f "$DB_PATH" ]; then
        echo "DB file not found: $DB_PATH"
        exit 1
    fi
}

list_tables_raw() {
    sqlite3 "$DB_PATH" \
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name;"
}

show_table_list() {
    echo ""
    echo "테이블 목록"
    sqlite3 -header -column "$DB_PATH" \
        "SELECT name AS table_name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name;"
}

select_table() {
    local tables=()
    local table

    SELECTED_TABLE=""

    while IFS= read -r table; do
        if [ -n "$table" ]; then
            tables[${#tables[@]}]="$table"
        fi
    done < <(list_tables_raw)

    if [ "${#tables[@]}" -eq 0 ]; then
        echo "조회 가능한 테이블이 없습니다."
        return 1
    fi

    echo ""
    echo "조회할 테이블을 선택하세요."
    PS3="table> "
    select table in "${tables[@]}" "뒤로가기"; do
        if [ -z "$table" ]; then
            echo "번호를 다시 선택하세요."
            continue
        fi

        if [ "$table" = "뒤로가기" ]; then
            return 1
        fi

        SELECTED_TABLE="$table"
        return 0
    done
}

show_table_rows() {
    local row_count

    row_count=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM \"$SELECTED_TABLE\";")

    echo ""
    echo "테이블: $SELECTED_TABLE"
    echo "rows  : $row_count"

    if command -v less > /dev/null 2>&1; then
        sqlite3 -cmd ".headers on" -cmd ".mode column" "$DB_PATH" \
            "SELECT * FROM \"$SELECTED_TABLE\";" | less -FXSR
    else
        sqlite3 -cmd ".headers on" -cmd ".mode column" "$DB_PATH" \
            "SELECT * FROM \"$SELECTED_TABLE\";"
    fi
}

show_session_status() {
    local attach_sql=""
    local join_sql=""
    local select_sql=""
    local codex_state_db_sql

    if [ -f "$CODEX_STATE_DB" ]; then
        codex_state_db_sql=$(printf "%s" "$CODEX_STATE_DB" | sed "s/'/''/g")
        attach_sql="ATTACH DATABASE '$codex_state_db_sql' AS codex_state;"
        join_sql="LEFT JOIN codex_state.threads ct ON ct.id = s.provider_session_id"
        select_sql="
            ct.title AS codex_title,
            ct.model AS local_model,
            ct.reasoning_effort AS local_reasoning,
            ct.updated_at AS local_updated_epoch,
            datetime(ct.updated_at, 'unixepoch', 'localtime') AS local_latest_used_at,"
    else
        select_sql="
            NULL AS codex_title,
            NULL AS local_model,
            NULL AS local_reasoning,
            NULL AS local_updated_epoch,
            NULL AS local_latest_used_at,"
    fi

    echo ""
    echo "현재 세션 현황"
    echo "bot db      : $DB_PATH"
    echo "codex state : $CODEX_STATE_DB"

    sqlite3 -cmd ".headers on" -cmd ".mode column" "$DB_PATH" "
        $attach_sql
        WITH message_stats AS (
            SELECT
                session_id,
                MAX(request_at) AS latest_request_at,
                (
                    SELECT chat_id
                    FROM message_log ml2
                    WHERE ml2.session_id = message_log.session_id
                    ORDER BY request_at DESC, id DESC
                    LIMIT 1
                ) AS latest_chat_id,
                COUNT(*) AS message_count,
                SUM(CASE WHEN processed = 0 THEN 1 ELSE 0 END) AS pending_count,
                SUM(CASE WHEN error IS NOT NULL AND error <> '' THEN 1 ELSE 0 END) AS error_count
            FROM message_log
            GROUP BY session_id
        )
        SELECT
            s.id AS telegram_session_id,
            s.provider_session_id AS local_session_id,
            datetime(
                MAX(
                    CAST(COALESCE(strftime('%s', s.last_used), 0) AS INTEGER),
                    CAST(COALESCE(s.local_updated_epoch, 0) AS INTEGER),
                    CAST(COALESCE(strftime('%s', ms.latest_request_at), 0) AS INTEGER)
                ),
                'unixepoch',
                'localtime'
            ) AS latest_used_at,
            CASE
                WHEN length(COALESCE(NULLIF(s.name, ''), NULLIF(s.codex_title, ''), '(no title)')) > 36
                THEN substr(COALESCE(NULLIF(s.name, ''), NULLIF(s.codex_title, ''), '(no title)'), 1, 33) || '...'
                ELSE COALESCE(NULLIF(s.name, ''), NULLIF(s.codex_title, ''), '(no title)')
            END AS title,
            s.ai_provider AS agency,
            s.model AS bot_model,
            CASE
                WHEN length(COALESCE(codex_title, '')) > 36
                THEN substr(codex_title, 1, 33) || '...'
                ELSE codex_title
            END AS local_title,
            local_model,
            local_reasoning,
            local_latest_used_at,
            s.user_id,
            ms.latest_chat_id AS chat_id,
            CASE
                WHEN ups.current_session_id = s.id THEN 'current'
                WHEN u.current_session_id = s.id THEN 'current'
                ELSE ''
            END AS current,
            CASE WHEN sl.session_id IS NOT NULL THEN 'locked' ELSE '' END AS lock,
            COALESCE(ms.message_count, 0) AS messages,
            COALESCE(ms.pending_count, 0) AS pending,
            COALESCE(ms.error_count, 0) AS errors,
            datetime(ms.latest_request_at, 'localtime') AS latest_request_at,
            s.deleted,
            s.recycled,
            s.workspace_path AS workspace_path
        FROM (
            SELECT
                s.*,
                $select_sql
                1 AS _dummy
            FROM sessions s
            $join_sql
        ) s
        LEFT JOIN users u ON u.id = s.user_id
        LEFT JOIN user_provider_state ups
            ON ups.user_id = s.user_id
           AND ups.ai_provider = s.ai_provider
        LEFT JOIN session_locks sl ON sl.session_id = s.id
        LEFT JOIN message_stats ms ON ms.session_id = s.id
        ORDER BY
            MAX(
                CAST(COALESCE(strftime('%s', s.last_used), 0) AS INTEGER),
                CAST(COALESCE(s.local_updated_epoch, 0) AS INTEGER),
                CAST(COALESCE(strftime('%s', ms.latest_request_at), 0) AS INTEGER)
            ) DESC,
            s.last_used DESC
        LIMIT 50;
    " | {
        if command -v less > /dev/null 2>&1; then
            less -FXSR
        else
            cat
        fi
    }
}

open_sql_shell() {
    echo ""
    echo "sqlite3 셸"
    echo "종료: .quit"
    sqlite3 -cmd ".headers on" -cmd ".mode column" "$DB_PATH"
}

main_menu() {
    local choice

    while true; do
        echo ""
        echo "=== SQLite Menu ==="
        echo "DB: $DB_PATH"
        echo "1. 테이블 리스트"
        echo "2. 테이블 선택 -> 테이블 전체 조회"
        echo "3. 쿼리 작성"
        echo "4. 현재 세션 현황"
        echo "5. 종료"
        printf "선택> "

        if ! read -r choice; then
            echo ""
            exit 0
        fi

        case "$choice" in
          1)
            show_table_list
            ;;
          2)
            if select_table; then
                show_table_rows
            fi
            ;;
          3)
            open_sql_shell
            ;;
          4)
            show_session_status
            ;;
          5|q|quit|exit)
            exit 0
            ;;
          *)
            echo "1, 2, 3, 4, 5 중에서 선택하세요."
            ;;
        esac
    done
}

require_sqlite3
require_db_file

case "${1:-}" in
  sessions|session-status|status)
    show_session_status
    exit 0
    ;;
esac

main_menu
