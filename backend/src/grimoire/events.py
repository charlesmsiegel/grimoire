"""Typed event type constants for the EventBus.

Every event type emitted or subscribed to in the codebase should have
a constant here. Using these constants instead of string literals
prevents typos and makes event discovery possible via IDE search.
"""

# Turn lifecycle
TURN_STARTED = "turn_started"
TURN_COMPLETE = "turn_complete"
TURN_UNDONE = "turn_undone"
TURN_TIMED_OUT = "turn_timed_out"
TURN_CANCELLED = "turn_cancelled"
TURN_FAILED = "turn_failed"
CONTEXT_BUILT = "context_built"
MODEL_RESPONSE_RECEIVED = "model_response_received"
DELTAS_EXTRACTED = "deltas_extracted"
DELTAS_APPLIED = "deltas_applied"
INTEGRATED_DELTAS_FALLBACK = "integrated_deltas_fallback"
PRE_ROLL_PENDING = "pre_roll_pending"
SPEAKER_ROUND_WAITING = "speaker_round_waiting"
SPEAKER_ROUND_NEXT = "speaker_round_next"
TURN_AUDIT_FRAGMENT = "turn_audit_fragment"
PENDING_CAST_CHANGES = "pending_cast_changes"

# Scene lifecycle
SCENE_STARTED = "scene_started"
SCENE_ENDED = "scene_ended"
SCENE_DELETED = "scene_deleted"
SCENE_BREAK_SUGGESTED = "scene_break_suggested"
POST_APPENDED = "post_appended"
PC_POST_APPENDED = "pc_post_appended"
POST_EDITED = "post_edited"
POST_DELETED = "post_deleted"
ADVANCE_REQUESTED = "advance_requested"
ADVANCE_DISABLED = "advance_disabled"
ADVANCE_ENABLED = "advance_enabled"
RUNNING_SUMMARY_UPDATED = "running_summary_updated"
RUNNING_SUMMARY_DUE = "running_summary_due"

# LLM
LLM_REQUEST_STARTED = "llm_request_started"
LLM_RESPONSE_RECEIVED = "llm_response_received"
LLM_REQUEST_FAILED = "llm_request_failed"
TIER_RESOLVED = "tier_resolved"
EMBEDDING_REQUEST_STARTED = "embedding_request_started"
EMBEDDING_RESPONSE_RECEIVED = "embedding_response_received"

# Alternates
ALTERNATE_ADDED = "alternate_added"
PRIMARY_SWITCHED = "primary_switched"
ALTERNATE_PINNED = "alternate_pinned"
ALTERNATE_DELETED = "alternate_deleted"

# Continuity
FACT_RECORDED = "fact_recorded"
CONTRADICTION_DETECTED = "contradiction_detected"
COMMITMENT_CREATED = "commitment_created"
COMMITMENT_PAID_OFF = "commitment_paid_off"
COMMITMENT_BROKEN = "commitment_broken"

# Inventory (#444)
INVENTORY_CHANGED = "inventory_changed"
INVENTORY_FLAGGED = "inventory_flagged"
COMMITMENT_REOPENED = "commitment_reopened"
COMMITMENT_OVERDUE = "commitment_overdue"
COMMITMENT_STALE = "commitment_stale"
DRIFT_DETECTED = "drift_detected"
THREAD_INTRODUCED = "thread_introduced"
THREAD_PAID_OFF = "thread_paid_off"
THREAD_OPENED = "thread_opened"
THREAD_CLOSED = "thread_closed"

# ImageGen
IMAGEGEN_JOB_QUEUED = "imagegen_job_queued"
IMAGEGEN_JOB_STARTED = "imagegen_job_started"
IMAGEGEN_JOB_FAILED = "imagegen_job_failed"
IMAGEGEN_PROGRESS = "imagegen_progress"
IMAGEGEN_DOWNLOAD_PROGRESS = "imagegen_download_progress"
IMAGE_READY = "image_ready"
IMAGEGEN_WARNING = "imagegen_warning"
IMAGEGEN_BACKEND_HEALTH_CHANGED = "imagegen_backend_health_changed"

# Fork
CAMPAIGN_FORK_STARTED = "campaign_fork_started"
CAMPAIGN_FORK_FAILED = "campaign_fork_failed"
CAMPAIGN_FORKED = "campaign_forked"
CAMPAIGN_FORK_QUEUED = "campaign_fork_queued"

# Time engine
TIME_ADVANCE = "time_advance"
TIME_ADVANCED = "time_advanced"
TIME_ADVANCE_CHECKPOINT_SUGGESTED = "time_advance_checkpoint_suggested"
NPC_TICK_COMPLETE = "npc_tick_complete"
NPC_DRIFT_DETECTED = "npc_drift_detected"
SCHEDULED_EVENT_IMMINENT = "scheduled_event_imminent"
WEATHER_CHANGED = "weather_changed"

# Library & watcher
LIBRARY_INDEXED = "library_indexed"
WATCHER_ERROR = "watcher_error"
LIBRARY_FILE_CHANGED = "library_file_changed"
CAMPAIGN_FILE_CHANGED = "campaign_file_changed"
SCENE_FILE_CHANGED = "scene_file_changed"
SHEET_FILE_CHANGED = "sheet_file_changed"
LIBRARY_REF_UPGRADED = "library_ref_upgraded"
LIBRARY_RENAME_DETECTED = "library_rename_detected"
CAMPAIGN_RENAME_DETECTED = "campaign_rename_detected"
LIBRARY_ENTITY_PROMOTED = "library_entity_promoted"
LIBRARY_ENTITY_DEMOTED = "library_entity_demoted"
LIBRARY_ENTITY_SAVE_BACK = "library_entity_save_back"
LIBRARY_RECLASSIFY = "library.reclassify"
LIBRARY_RECLASSIFY_UNDO = "library.reclassify_undo"
ENTITY_PROMOTED = "entity_promoted"

# Mechanics & plugins
MECHANICS_SWITCHED = "mechanics_switched"
MECHANICS_EVENT = "mechanics_event"
PROVIDER_HEALTH_CHANGED = "provider_health_changed"
PLUGIN_LOADED = "plugin_loaded"
PLUGIN_FAILED = "plugin_failed"
PLUGIN_UNLOADED = "plugin_unloaded"
PLUGIN_ACTIVATED = "plugin_activated"
PLUGIN_DEACTIVATED = "plugin_deactivated"
PLUGIN_HEALTH_CHANGED = "plugin_health_changed"

# Observability
HEALTH_STATUS_CHANGED = "health_status_changed"
ERROR_REPORTED = "error_reported"

# Review
REVIEW_ITEM_ADDED = "review_item_added"
REVIEW_ITEM_RESOLVED = "review_item_resolved"

# Retcon
RETCON_STARTED = "retcon_started"
RETCON_POST_REPLAYED = "retcon_post_replayed"
RETCON_POST_ACCEPTED = "retcon_post_accepted"
RETCON_CANCELLED = "retcon_cancelled"
RETCON_COMPLETE = "retcon_complete"

# Background workers
BACKUP_COMPLETE = "backup_complete"
RETENTION_SWEEP_COMPLETED = "retention_sweep_completed"
EMBEDDING_PROGRESS = "embedding_progress"
LIBRARY_SUMMARY_PROGRESS = "library_summary_progress"
