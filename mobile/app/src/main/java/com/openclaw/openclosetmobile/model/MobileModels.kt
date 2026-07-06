package com.openclaw.openclosetmobile.model

data class WorkspaceSummary(
    val id: String,
    val name: String,
    val description: String? = null,
    val status: String,
    val kind: String,
    val attention_profile: Map<String, Any?>? = null,
)

data class BuildProjectSummary(
    val id: String,
    val workspace_id: String,
    val name: String,
    val description: String? = null,
    val status: String,
)

data class ProjectDeliverySummary(
    val id: String,
    val workspace_id: String,
    val build_project_id: String,
    val capture_id: String? = null,
    val target_device_id: String? = null,
    val artifact_kind: String,
    val file_name: String,
    val mime_type: String,
    val size_bytes: Long,
    val status: String,
    val metadata: Map<String, Any?> = emptyMap(),
    val created_at: String,
    val updated_at: String,
    val downloaded_at: String? = null,
    val installed_at: String? = null,
    val download_url: String,
    val ack_url: String,
)

data class CaptureSummary(
    val id: String,
    val workspace_id: String? = null,
    val build_project_id: String? = null,
    val session_id: String? = null,
    val source: String,
    val event_type: String,
    val content: String,
    val media_url: String? = null,
    val metadata: Map<String, Any?> = emptyMap(),
    val status: String,
    val received_at: String,
    val processed_at: String? = null,
)

data class SessionRunSummary(
    val id: String,
    val status: String,
    val turn_number: Int,
)

data class MobileSessionSummary(
    val id: String,
    val label: String,
    val model: String,
    val provider: String,
    val status: String,
    val token_count: Int,
    val context_window: Int,
    val workspace_id: String? = null,
    val build_project_id: String? = null,
    val created_at: String,
    val updated_at: String,
    val current_run: SessionRunSummary? = null,
)

data class MobileSessionEventSummary(
    val id: String,
    val session_id: String,
    val workspace_id: String? = null,
    val build_project_id: String? = null,
    val run_id: String? = null,
    val type: String,
    val data: Map<String, Any?> = emptyMap(),
    val created_at: String,
)

data class TranscriptMessageSummary(
    val id: String,
    val role: String,
    val content: String,
    val position: Int,
    val token_estimate: Int,
    val created_at: String,
    val archive_ready: Boolean = false,
    val archive_state: Map<String, Any?>? = null,
)

data class BehaviorFeedbackRecord(
    val id: String,
    val session_id: String,
    val workspace_id: String? = null,
    val build_project_id: String? = null,
    val message_id: String,
    val signal: String,
    val message_preview: String,
    val traits: List<String> = emptyList(),
    val created_at: String,
    val updated_at: String,
)

data class BehaviorPatchRecord(
    val id: String,
    val session_id: String,
    val workspace_id: String? = null,
    val build_project_id: String? = null,
    val scope: String,
    val scope_id: String,
    val rule_key: String,
    val title: String,
    val patch: String,
    val status: String,
    val created_by: String,
    val created_at: String,
    val updated_at: String,
)

data class BehaviorDismissalRecord(
    val session_id: String,
    val rule_key: String,
    val created_at: String,
    val updated_at: String,
)

data class BehaviorStateRecord(
    val session_id: String,
    val feedback: List<BehaviorFeedbackRecord> = emptyList(),
    val patches: List<BehaviorPatchRecord> = emptyList(),
    val dismissals: List<BehaviorDismissalRecord> = emptyList(),
)

data class MobileBootstrap(
    val device_id: String,
    val workspaces: List<WorkspaceSummary> = emptyList(),
    val projects_by_workspace: Map<String, List<BuildProjectSummary>> = emptyMap(),
    val deliveries_by_workspace: Map<String, List<ProjectDeliverySummary>> = emptyMap(),
    val sessions_by_workspace: Map<String, List<MobileSessionSummary>> = emptyMap(),
    val recent_captures: List<CaptureSummary> = emptyList(),
    val recent_session_events: List<MobileSessionEventSummary> = emptyList(),
    val generated_at: String,
)

data class ProviderRecord(
    val id: String,
    val kind: String,
    val base_url: String,
    val model_name: String? = null,
    val timeout_sec: Int = 60,
    val enabled: Boolean = true,
    val capabilities: Map<String, Any?> = emptyMap(),
    val has_api_key: Boolean = false,
    val last_health_status: String? = null,
    val last_health_at: String? = null,
)

data class ProviderModelsResponse(
    val provider_id: String,
    val models: List<String> = emptyList(),
    val discovered: Boolean = false,
    val error: String? = null,
)

data class ChatSessionRecord(
    val id: String,
    val label: String = "",
    val model: String,
    val provider: String,
    val context_window: Int = 0,
    val status: String,
    val workspace_id: String? = null,
    val build_project_id: String? = null,
)

data class MessagePostResponse(
    val message_id: String,
    val run_id: String,
    val turn_number: Int = 0,
    val session_id: String,
    val role: String = "user",
    val content: String = "",
    val position: Int = 0,
    val status: String = "queued",
)

data class MobileSettings(
    val backendUrl: String = "",
    val deviceId: String = "",
    val preferredWorkspaceId: String = "",
    val pocketPalPackage: String = "",
    val cachedBootstrapJson: String = "",
    val lastNotifiedDeliveryIds: Set<String> = emptySet(),
) {
    val isConfigured: Boolean
        get() = backendUrl.isNotBlank() && deviceId.isNotBlank()
}