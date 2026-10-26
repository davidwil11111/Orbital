CREATE TABLE local_runtime_sessions (
  session_id TEXT PRIMARY KEY, record_json TEXT NOT NULL, updated_at_ms INTEGER NOT NULL,
  agent_name TEXT, runtime TEXT, session_type TEXT, status TEXT,
  archived INTEGER NOT NULL DEFAULT 0, visibility TEXT NOT NULL DEFAULT 'visible',
  session_kind TEXT NOT NULL DEFAULT 'unknown', parent_session_id TEXT,
  workspace_dir TEXT, project_workspace_dir TEXT, title TEXT, created_at_ms INTEGER,
  error_message TEXT, error_code TEXT, history_relative_dir TEXT);
CREATE TABLE local_runtime_message_rows (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, msg_id TEXT NOT NULL,
  role TEXT, turn_id TEXT, created_at_ms INTEGER NOT NULL, data_json TEXT NOT NULL,
  source TEXT, source_context_json TEXT, UNIQUE(session_id, msg_id));
