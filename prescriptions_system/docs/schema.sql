-- DDL схемы prescriptions_system (SQLite), сгенерировано из ORM-моделей.
-- Источник: src/db/models.py + первая ревизия Alembic. Не редактировать вручную.
-- PRAGMA для подключения: journal_mode=WAL; foreign_keys=ON;

CREATE TABLE contractors (
	id INTEGER NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	inn VARCHAR(12), 
	is_active BOOLEAN NOT NULL, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_contractors_name ON contractors (name);

CREATE TABLE norm_docs (
	id INTEGER NOT NULL, 
	code VARCHAR(120) NOT NULL, 
	title VARCHAR(500) NOT NULL, 
	edition VARCHAR(120), 
	status VARCHAR(40) NOT NULL, 
	source_file VARCHAR(500) NOT NULL, 
	ingested_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_norm_docs_code ON norm_docs (code);

CREATE TABLE objects (
	id INTEGER NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	address VARCHAR(500), 
	is_active BOOLEAN NOT NULL, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_objects_name ON objects (name);

CREATE TABLE recipient_channels (
	id INTEGER NOT NULL, 
	recipient_type VARCHAR(16) NOT NULL, 
	recipient_id INTEGER NOT NULL, 
	channel VARCHAR(8) NOT NULL, 
	address VARCHAR(128) NOT NULL, 
	is_preferred BOOLEAN NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_recipient_channel UNIQUE (recipient_type, recipient_id, channel, address)
);
CREATE INDEX ix_recipient_channels_recipient ON recipient_channels (recipient_type, recipient_id);

CREATE TABLE system_config (
	id INTEGER NOT NULL, 
	"key" VARCHAR(64) NOT NULL, 
	value JSON, 
	description VARCHAR(255), 
	updated_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_system_config_key ON system_config ("key");

CREATE TABLE norm_chunks (
	id INTEGER NOT NULL, 
	document_id INTEGER NOT NULL, 
	section VARCHAR(255), 
	item VARCHAR(255), 
	text TEXT NOT NULL, 
	chunk_index INTEGER NOT NULL, 
	start_char INTEGER, 
	end_char INTEGER, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_norm_chunk_index UNIQUE (document_id, chunk_index), 
	CONSTRAINT fk_chunks_document FOREIGN KEY(document_id) REFERENCES norm_docs (id)
);
CREATE INDEX ix_norm_chunks_document_id ON norm_chunks (document_id);

CREATE TABLE users (
	id INTEGER NOT NULL, 
	login VARCHAR(64) NOT NULL, 
	full_name VARCHAR(255) NOT NULL, 
	role VARCHAR(22) NOT NULL, 
	email VARCHAR(255), 
	phone VARCHAR(32), 
	preferred_notification_channel VARCHAR(8), 
	contractor_id INTEGER, 
	object_id INTEGER, 
	password_hash VARCHAR(128) NOT NULL, 
	must_change_password BOOLEAN NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	updated_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_users_contractor FOREIGN KEY(contractor_id) REFERENCES contractors (id), 
	CONSTRAINT fk_users_object FOREIGN KEY(object_id) REFERENCES objects (id)
);
CREATE INDEX ix_users_contractor_id ON users (contractor_id);
CREATE UNIQUE INDEX ix_users_login ON users (login);
CREATE INDEX ix_users_object_id ON users (object_id);

CREATE TABLE norm_chunk_embeddings (
	id INTEGER NOT NULL, 
	chunk_id INTEGER NOT NULL, 
	chroma_id VARCHAR(128) NOT NULL, 
	model VARCHAR(120) NOT NULL, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_embeddings_chunk FOREIGN KEY(chunk_id) REFERENCES norm_chunks (id), 
	UNIQUE (chroma_id)
);
CREATE UNIQUE INDEX ix_norm_chunk_embeddings_chunk_id ON norm_chunk_embeddings (chunk_id);

CREATE TABLE prescriptions (
	id INTEGER NOT NULL, 
	prescription_type VARCHAR(8) NOT NULL, 
	doc_number VARCHAR(64) NOT NULL, 
	doc_date DATE NOT NULL, 
	object_id INTEGER NOT NULL, 
	contractor_id INTEGER NOT NULL, 
	description TEXT NOT NULL, 
	deadline DATE, 
	responsible_user_id INTEGER, 
	inspector_user_id INTEGER, 
	status VARCHAR(20) NOT NULL, 
	source_file_path VARCHAR(500), 
	source_file_hash VARCHAR(64), 
	ocr_text TEXT, 
	ocr_confidence FLOAT, 
	requires_review BOOLEAN NOT NULL, 
	confirmed_by_user_id INTEGER, 
	confirmed_at DATETIME, 
	closed_by_user_id INTEGER, 
	closed_at DATETIME, 
	created_by_user_id INTEGER, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	updated_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_prescription_doc UNIQUE (doc_number, doc_date, object_id, prescription_type), 
	CONSTRAINT fk_prescriptions_object FOREIGN KEY(object_id) REFERENCES objects (id), 
	CONSTRAINT fk_prescriptions_contractor FOREIGN KEY(contractor_id) REFERENCES contractors (id), 
	CONSTRAINT fk_prescriptions_responsible_user FOREIGN KEY(responsible_user_id) REFERENCES users (id), 
	CONSTRAINT fk_prescriptions_inspector_user FOREIGN KEY(inspector_user_id) REFERENCES users (id), 
	CONSTRAINT fk_prescriptions_confirmed_by FOREIGN KEY(confirmed_by_user_id) REFERENCES users (id), 
	CONSTRAINT fk_prescriptions_closed_by FOREIGN KEY(closed_by_user_id) REFERENCES users (id), 
	CONSTRAINT fk_prescriptions_created_by FOREIGN KEY(created_by_user_id) REFERENCES users (id)
);
CREATE INDEX ix_prescriptions_closed_by_user_id ON prescriptions (closed_by_user_id);
CREATE INDEX ix_prescriptions_confirmed_by_user_id ON prescriptions (confirmed_by_user_id);
CREATE INDEX ix_prescriptions_contractor_id ON prescriptions (contractor_id);
CREATE INDEX ix_prescriptions_created_by_user_id ON prescriptions (created_by_user_id);
CREATE INDEX ix_prescriptions_doc_number ON prescriptions (doc_number);
CREATE INDEX ix_prescriptions_inspector_user_id ON prescriptions (inspector_user_id);
CREATE INDEX ix_prescriptions_object_id ON prescriptions (object_id);
CREATE INDEX ix_prescriptions_responsible_user_id ON prescriptions (responsible_user_id);
CREATE INDEX ix_prescriptions_status_deadline ON prescriptions (status, deadline);

CREATE TABLE user_role_change_log (
	id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	old_role VARCHAR(32) NOT NULL, 
	new_role VARCHAR(32) NOT NULL, 
	changed_by_user_id INTEGER, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_role_log_user FOREIGN KEY(user_id) REFERENCES users (id), 
	CONSTRAINT fk_role_log_changed_by FOREIGN KEY(changed_by_user_id) REFERENCES users (id)
);
CREATE INDEX ix_user_role_change_log_changed_by_user_id ON user_role_change_log (changed_by_user_id);
CREATE INDEX ix_user_role_change_log_user_id ON user_role_change_log (user_id);

CREATE TABLE llm_rag_logs (
	id INTEGER NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	purpose VARCHAR(40) NOT NULL, 
	model VARCHAR(120) NOT NULL, 
	prompt_version VARCHAR(40), 
	prescription_id INTEGER, 
	input_text TEXT NOT NULL, 
	output_text TEXT, 
	status VARCHAR(5) NOT NULL, 
	error_message TEXT, 
	duration_ms INTEGER, 
	confidence FLOAT, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_llm_logs_prescription FOREIGN KEY(prescription_id) REFERENCES prescriptions (id)
);
CREATE INDEX ix_llm_rag_logs_prescription_id ON llm_rag_logs (prescription_id);

CREATE TABLE notifications (
	id INTEGER NOT NULL, 
	prescription_id INTEGER NOT NULL, 
	event_type VARCHAR(38) NOT NULL, 
	event_version INTEGER NOT NULL, 
	recipient_type VARCHAR(16) NOT NULL, 
	recipient_id INTEGER NOT NULL, 
	channel VARCHAR(8), 
	message_text TEXT NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	attempt_count INTEGER NOT NULL, 
	last_error TEXT, 
	external_message_id VARCHAR(128), 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	sent_at DATETIME, 
	next_attempt_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_notification_event_key UNIQUE (prescription_id, event_type, recipient_type, recipient_id, event_version), 
	CONSTRAINT fk_notifications_prescription FOREIGN KEY(prescription_id) REFERENCES prescriptions (id)
);
CREATE INDEX ix_notifications_prescription_id ON notifications (prescription_id);
CREATE INDEX ix_notifications_status_next_attempt ON notifications (status, next_attempt_at);

CREATE TABLE prescription_history (
	id INTEGER NOT NULL, 
	prescription_id INTEGER NOT NULL, 
	changed_by_user_id INTEGER, 
	change_type VARCHAR(64) NOT NULL, 
	old_value JSON, 
	new_value JSON, 
	comment TEXT, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_history_prescription FOREIGN KEY(prescription_id) REFERENCES prescriptions (id), 
	CONSTRAINT fk_history_changed_by FOREIGN KEY(changed_by_user_id) REFERENCES users (id)
);
CREATE INDEX ix_prescription_history_changed_by_user_id ON prescription_history (changed_by_user_id);
CREATE INDEX ix_prescription_history_prescription_id ON prescription_history (prescription_id);

CREATE TABLE prescription_items (
	id INTEGER NOT NULL, 
	prescription_id INTEGER NOT NULL, 
	item_number INTEGER, 
	text TEXT NOT NULL, 
	deadline DATE, 
	explicit_norm_refs JSON, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_items_prescription FOREIGN KEY(prescription_id) REFERENCES prescriptions (id)
);
CREATE INDEX ix_prescription_items_prescription_id ON prescription_items (prescription_id);

CREATE TABLE processed_files (
	id INTEGER NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	original_path VARCHAR(500) NOT NULL, 
	status VARCHAR(9) NOT NULL, 
	prescription_id INTEGER, 
	error TEXT, 
	processed_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_processed_files_prescription FOREIGN KEY(prescription_id) REFERENCES prescriptions (id)
);
CREATE INDEX ix_processed_files_prescription_id ON processed_files (prescription_id);
CREATE UNIQUE INDEX ix_processed_files_sha256 ON processed_files (sha256);

CREATE TABLE notification_attempts (
	id INTEGER NOT NULL, 
	notification_id INTEGER NOT NULL, 
	attempt_number INTEGER NOT NULL, 
	channel VARCHAR(8), 
	success BOOLEAN NOT NULL, 
	error TEXT, 
	external_message_id VARCHAR(128), 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_attempts_notification FOREIGN KEY(notification_id) REFERENCES notifications (id)
);
CREATE INDEX ix_notification_attempts_notification_id ON notification_attempts (notification_id);

CREATE TABLE prescription_attachments (
	id INTEGER NOT NULL, 
	prescription_id INTEGER NOT NULL, 
	item_id INTEGER, 
	kind VARCHAR(5) NOT NULL, 
	file_path VARCHAR(500) NOT NULL, 
	file_hash VARCHAR(64) NOT NULL, 
	size_bytes INTEGER, 
	caption VARCHAR(500), 
	uploaded_by_user_id INTEGER, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_attachments_prescription FOREIGN KEY(prescription_id) REFERENCES prescriptions (id), 
	CONSTRAINT fk_attachments_item FOREIGN KEY(item_id) REFERENCES prescription_items (id), 
	CONSTRAINT fk_attachments_uploaded_by FOREIGN KEY(uploaded_by_user_id) REFERENCES users (id)
);
CREATE INDEX ix_prescription_attachments_file_hash ON prescription_attachments (file_hash);
CREATE INDEX ix_prescription_attachments_item_id ON prescription_attachments (item_id);
CREATE INDEX ix_prescription_attachments_prescription_id ON prescription_attachments (prescription_id);
CREATE INDEX ix_prescription_attachments_uploaded_by_user_id ON prescription_attachments (uploaded_by_user_id);

CREATE TABLE prescription_norm_refs (
	id INTEGER NOT NULL, 
	prescription_id INTEGER NOT NULL, 
	item_id INTEGER, 
	doc_code VARCHAR(120) NOT NULL, 
	doc_item VARCHAR(120), 
	title VARCHAR(500), 
	fragment TEXT, 
	chunk_id INTEGER, 
	source VARCHAR(8) NOT NULL, 
	status VARCHAR(9) NOT NULL, 
	confirmed_by_user_id INTEGER, 
	confirmed_at DATETIME, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_normrefs_prescription FOREIGN KEY(prescription_id) REFERENCES prescriptions (id), 
	CONSTRAINT fk_normrefs_item FOREIGN KEY(item_id) REFERENCES prescription_items (id), 
	CONSTRAINT fk_normrefs_chunk FOREIGN KEY(chunk_id) REFERENCES norm_chunks (id), 
	CONSTRAINT fk_normrefs_confirmed_by FOREIGN KEY(confirmed_by_user_id) REFERENCES users (id)
);
CREATE INDEX ix_prescription_norm_refs_chunk_id ON prescription_norm_refs (chunk_id);
CREATE INDEX ix_prescription_norm_refs_confirmed_by_user_id ON prescription_norm_refs (confirmed_by_user_id);
CREATE INDEX ix_prescription_norm_refs_doc_code ON prescription_norm_refs (doc_code);
CREATE INDEX ix_prescription_norm_refs_item_id ON prescription_norm_refs (item_id);
CREATE INDEX ix_prescription_norm_refs_prescription_id ON prescription_norm_refs (prescription_id);
