package com.openclaw.openclosetmobile

import android.app.Application
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Send
import androidx.compose.material.icons.outlined.Download
import androidx.compose.material.icons.outlined.HourglassBottom
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Star
import androidx.compose.material.icons.outlined.ThumbDown
import androidx.compose.material.icons.outlined.ThumbUp
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.rememberSwipeToDismissBoxState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import com.openclaw.openclosetmobile.data.ChatStreamEvent
import com.openclaw.openclosetmobile.data.MobileRepository
import com.openclaw.openclosetmobile.data.SettingsStore
import com.openclaw.openclosetmobile.logging.MobileLog
import com.openclaw.openclosetmobile.model.BehaviorDismissalRecord
import com.openclaw.openclosetmobile.model.BehaviorFeedbackRecord
import com.openclaw.openclosetmobile.model.BehaviorPatchRecord
import com.openclaw.openclosetmobile.model.BehaviorStateRecord
import com.openclaw.openclosetmobile.model.MobileBootstrap
import com.openclaw.openclosetmobile.model.MobileSettings
import com.openclaw.openclosetmobile.model.MobileSessionEventSummary
import com.openclaw.openclosetmobile.model.MobileSessionSummary
import com.openclaw.openclosetmobile.model.ProjectDeliverySummary
import com.openclaw.openclosetmobile.model.ProviderRecord
import com.openclaw.openclosetmobile.model.TranscriptMessageSummary
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        MobileLog.i(MobileLog.TAG_STARTUP, "startup.activity.onCreate.begin")
        super.onCreate(savedInstanceState)
        runCatching { enableEdgeToEdge() }.onFailure { error ->
            MobileLog.e(MobileLog.TAG_STARTUP, "startup.activity.enableEdgeToEdge.failed", throwable = error)
        }
        MobileLog.i(MobileLog.TAG_STARTUP, "startup.activity.setContent.begin")
        setContent {
            MaterialTheme {
                LaunchedEffect(Unit) {
                    MobileLog.i(MobileLog.TAG_STARTUP, "startup.compose.root.begin")
                }
                OpenClosetMobileRoot()
            }
        }
        MobileLog.i(MobileLog.TAG_STARTUP, "startup.activity.onCreate.end")
    }
}

internal data class MobileUiState(
    val settings: MobileSettings = MobileSettings(),
    val bootstrap: MobileBootstrap? = null,
    val behavior: MobileBehaviorPaneState = MobileBehaviorPaneState(),
    val chat: MobileChatPaneState = MobileChatPaneState(),
    val isRefreshing: Boolean = false,
    val isSubmitting: Boolean = false,
    val message: String? = null,
    val error: String? = null,
)

internal data class MobileChatPaneState(
    val providers: List<ProviderRecord> = emptyList(),
    val modelsByProvider: Map<String, List<String>> = emptyMap(),
    val selectedProviderId: String? = null,
    val selectedModel: String? = null,
    val activeSessionId: String? = null,
    val activeRunId: String? = null,
    val transcript: List<TranscriptMessageSummary> = emptyList(),
    val streamingAssistantText: String = "",
    val streamingThinkingText: String = "",
    val isStreaming: Boolean = false,
    val isLoadingProviders: Boolean = false,
    val isSendingMessage: Boolean = false,
    val errorMessage: String? = null,
)

internal data class MobileBehaviorPaneState(
    val sessionId: String? = null,
    val messages: List<TranscriptMessageSummary> = emptyList(),
    val behaviorState: BehaviorStateRecord = BehaviorStateRecord(session_id = ""),
    val isLoading: Boolean = false,
    val isSaving: Boolean = false,
)

internal data class BehaviorScopeOption(
    val scope: String,
    val label: String,
    val scopeId: String,
)

internal data class MobileBehaviorProposal(
    val ruleKey: String,
    val title: String,
    val observedPattern: String,
    val hypothesis: String,
    val patch: String,
    val recommendedScope: String,
    val sampleCount: Int,
)

internal class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val settingsStore = SettingsStore(application)
    private val repository by lazy(LazyThreadSafetyMode.NONE) {
        MobileLog.i(MobileLog.TAG_STARTUP, "startup.repository.lazyInit.begin")
        runCatching { MobileRepository(application) }
            .onSuccess { MobileLog.i(MobileLog.TAG_STARTUP, "startup.repository.lazyInit.ok") }
            .onFailure { error ->
                MobileLog.e(MobileLog.TAG_STARTUP, "startup.repository.lazyInit.failed", throwable = error)
            }
            .getOrThrow()
    }
    private val _uiState = MutableStateFlow(MobileUiState())
    val uiState: StateFlow<MobileUiState> = _uiState.asStateFlow()

    init {
        MobileLog.i(MobileLog.TAG_STARTUP, "startup.viewmodel.init.begin")
        viewModelScope.launch {
            MobileLog.i(MobileLog.TAG_STARTUP, "startup.settings.collect.begin")
            settingsStore.settings.collect { settings ->
                MobileLog.d(
                    MobileLog.TAG_STARTUP,
                    "startup.settings.tick",
                    "configured=${settings.isConfigured} hasCachedBootstrap=${settings.cachedBootstrapJson.isNotBlank()}",
                )
                MobileLog.d(MobileLog.TAG_STARTUP, "startup.bootstrap.parse.begin")
                val cachedBootstrap = runCatching {
                    repository.parseCachedBootstrap(settings.cachedBootstrapJson)
                }.onFailure { error ->
                    MobileLog.w(MobileLog.TAG_STARTUP, "startup.bootstrap.parse.failed", throwable = error)
                }.getOrNull()
                _uiState.value = _uiState.value.copy(
                    settings = settings,
                    bootstrap = cachedBootstrap ?: _uiState.value.bootstrap,
                )
                if (settings.isConfigured && _uiState.value.bootstrap == null) {
                    refresh()
                }
            }
        }
    }

    fun refresh() {
        val settings = _uiState.value.settings
        if (!settings.isConfigured) {
            MobileLog.w(MobileLog.TAG_NET, "refresh.skip.unconfigured")
            _uiState.value = _uiState.value.copy(error = "Configure backend URL and device ID first.")
            return
        }
        viewModelScope.launch {
            MobileLog.i(MobileLog.TAG_NET, "refresh.begin", "device=${settings.deviceId}")
            _uiState.value = _uiState.value.copy(isRefreshing = true, error = null, message = null)
            runCatching {
                val bootstrap = repository.refreshBootstrap(settings)
                settingsStore.updateCachedBootstrap(repository.encodeBootstrap(bootstrap))
                _uiState.value = _uiState.value.copy(
                    bootstrap = bootstrap,
                    isRefreshing = false,
                    message = "Synced ${bootstrap.workspaces.size} workspace(s).",
                )
                MobileLog.i(MobileLog.TAG_NET, "refresh.ok", "workspaces=${bootstrap.workspaces.size}")
            }.onFailure { error ->
                MobileLog.e(MobileLog.TAG_NET, "refresh.failed", throwable = error)
                _uiState.value = _uiState.value.copy(
                    isRefreshing = false,
                    error = error.message ?: "Refresh failed.",
                )
            }
        }
    }

    fun saveSettings(
        backendUrl: String,
        deviceId: String,
        preferredWorkspaceId: String,
        pocketPalPackage: String,
    ) {
        viewModelScope.launch {
            settingsStore.updateSettings(backendUrl, deviceId, preferredWorkspaceId, pocketPalPackage)
            _uiState.value = _uiState.value.copy(message = "Settings saved.")
        }
    }

    fun submitFeatureDraft(
        workspaceId: String,
        projectId: String?,
        sessionId: String?,
        ramble: String,
        distilledBrief: String,
        targetDevice: String,
        why: String,
        approvalNote: String,
    ) {
        val settings = _uiState.value.settings
        viewModelScope.launch {
            _uiState.value = _uiState.value.copy(isSubmitting = true, error = null, message = null)
            runCatching {
                repository.submitFeatureDraft(
                    settings = settings,
                    workspaceId = workspaceId,
                    projectId = projectId,
                    sessionId = sessionId,
                    distilledBrief = distilledBrief,
                    ramble = ramble,
                    targetDevice = targetDevice,
                    why = why,
                    approvalNote = approvalNote,
                )
            }.onSuccess {
                _uiState.value = _uiState.value.copy(
                    isSubmitting = false,
                    message = "Approved feature brief sent to OpenCloset.",
                )
                refresh()
            }.onFailure { error ->
                _uiState.value = _uiState.value.copy(
                    isSubmitting = false,
                    error = error.message ?: "Submission failed.",
                )
            }
        }
    }

    fun acknowledgeDelivery(workspaceId: String, deliveryId: String, status: String, note: String) {
        val settings = _uiState.value.settings
        viewModelScope.launch {
            runCatching {
                repository.acknowledgeDelivery(settings, workspaceId, deliveryId, status, note)
            }.onSuccess {
                _uiState.value = _uiState.value.copy(message = "Delivery updated to $status.")
                refresh()
            }.onFailure { error ->
                _uiState.value = _uiState.value.copy(error = error.message ?: "Delivery update failed.")
            }
        }
    }

    fun loadBehaviorSession(sessionId: String) {
        val settings = _uiState.value.settings
        if (!settings.isConfigured || sessionId.isBlank()) {
            return
        }
        viewModelScope.launch {
            _uiState.value = _uiState.value.copy(
                behavior = _uiState.value.behavior.copy(
                    sessionId = sessionId,
                    isLoading = true,
                ),
                error = null,
            )
            runCatching {
                val messages = repository.listSessionMessages(settings, sessionId)
                val behaviorState = repository.getBehaviorState(settings, sessionId)
                Pair(messages, behaviorState)
            }.onSuccess { (messages, behaviorState) ->
                _uiState.value = _uiState.value.copy(
                    behavior = _uiState.value.behavior.copy(
                        sessionId = sessionId,
                        messages = messages,
                        behaviorState = behaviorState,
                        isLoading = false,
                        isSaving = false,
                    ),
                )
            }.onFailure { error ->
                _uiState.value = _uiState.value.copy(
                    behavior = _uiState.value.behavior.copy(isLoading = false, isSaving = false),
                    error = error.message ?: "Behavior sync failed.",
                )
            }
        }
    }

    fun submitBehaviorFeedback(session: MobileSessionSummary, message: TranscriptMessageSummary, signal: String) {
        val settings = _uiState.value.settings
        val existing = _uiState.value.behavior.behaviorState.feedback.firstOrNull { it.message_id == message.id }
        viewModelScope.launch {
            _uiState.value = _uiState.value.copy(
                behavior = _uiState.value.behavior.copy(isSaving = true),
                error = null,
            )
            runCatching {
                if (existing?.signal == signal) {
                    repository.deleteBehaviorFeedback(settings, session.id, message.id)
                } else {
                    repository.upsertBehaviorFeedback(
                        settings = settings,
                        sessionId = session.id,
                        messageId = message.id,
                        signal = signal,
                        messagePreview = truncateBehaviorText(message.content),
                        traits = classifyAssistantTraits(message.content),
                    )
                }
            }.onSuccess {
                loadBehaviorSession(session.id)
            }.onFailure { error ->
                _uiState.value = _uiState.value.copy(
                    behavior = _uiState.value.behavior.copy(isSaving = false),
                    error = error.message ?: "Behavior feedback failed.",
                )
            }
        }
    }

    fun applyBehaviorProposal(session: MobileSessionSummary, proposal: MobileBehaviorProposal, scopeOption: BehaviorScopeOption) {
        val settings = _uiState.value.settings
        viewModelScope.launch {
            _uiState.value = _uiState.value.copy(
                behavior = _uiState.value.behavior.copy(isSaving = true),
                error = null,
            )
            runCatching {
                repository.applyBehaviorPatch(
                    settings = settings,
                    sessionId = session.id,
                    ruleKey = proposal.ruleKey,
                    title = proposal.title,
                    patch = proposal.patch,
                    scope = scopeOption.scope,
                    scopeId = scopeOption.scopeId,
                )
            }.onSuccess {
                loadBehaviorSession(session.id)
            }.onFailure { error ->
                _uiState.value = _uiState.value.copy(
                    behavior = _uiState.value.behavior.copy(isSaving = false),
                    error = error.message ?: "Behavior patch apply failed.",
                )
            }
        }
    }

    fun dismissBehaviorProposal(sessionId: String, ruleKey: String) {
        val settings = _uiState.value.settings
        viewModelScope.launch {
            _uiState.value = _uiState.value.copy(
                behavior = _uiState.value.behavior.copy(isSaving = true),
                error = null,
            )
            runCatching {
                repository.dismissBehaviorProposal(settings, sessionId, ruleKey)
            }.onSuccess {
                loadBehaviorSession(sessionId)
            }.onFailure { error ->
                _uiState.value = _uiState.value.copy(
                    behavior = _uiState.value.behavior.copy(isSaving = false),
                    error = error.message ?: "Behavior proposal dismissal failed.",
                )
            }
        }
    }

    fun loadChatProviders() {
        val settings = _uiState.value.settings
        if (!settings.isConfigured) {
            _uiState.value = _uiState.value.copy(error = "Configure backend URL first.")
            return
        }
        viewModelScope.launch {
            _uiState.value = _uiState.value.copy(
                chat = _uiState.value.chat.copy(isLoadingProviders = true, errorMessage = null),
            )
            runCatching { repository.listProviders(settings) }
                .onSuccess { providers ->
                    val enabled = providers.filter { it.enabled }
                    _uiState.value = _uiState.value.copy(
                        chat = _uiState.value.chat.copy(
                            providers = enabled,
                            isLoadingProviders = false,
                            selectedProviderId = _uiState.value.chat.selectedProviderId
                                ?: enabled.firstOrNull()?.id,
                        ),
                    )
                    enabled.forEach { provider -> loadModelsForProvider(provider.id) }
                }
                .onFailure { error ->
                    _uiState.value = _uiState.value.copy(
                        chat = _uiState.value.chat.copy(
                            isLoadingProviders = false,
                            errorMessage = error.message ?: "Failed to load providers.",
                        ),
                    )
                }
        }
    }

    private fun loadModelsForProvider(providerId: String) {
        val settings = _uiState.value.settings
        viewModelScope.launch {
            runCatching { repository.listProviderModels(settings, providerId) }
                .onSuccess { response ->
                    val current = _uiState.value.chat
                    val updatedMap = current.modelsByProvider + (providerId to response.models)
                    _uiState.value = _uiState.value.copy(
                        chat = current.copy(
                            modelsByProvider = updatedMap,
                            selectedModel = current.selectedModel
                                ?: if (current.selectedProviderId == providerId) response.models.firstOrNull() else current.selectedModel,
                        ),
                    )
                }
                .onFailure { error ->
                    MobileLog.w(MobileLog.TAG_NET, "chat.models.failed", "provider=$providerId", error)
                }
        }
    }

    fun selectChatProvider(providerId: String) {
        val current = _uiState.value.chat
        val firstModel = current.modelsByProvider[providerId]?.firstOrNull()
        _uiState.value = _uiState.value.copy(
            chat = current.copy(
                selectedProviderId = providerId,
                selectedModel = firstModel,
            ),
        )
        if (current.modelsByProvider[providerId] == null) {
            loadModelsForProvider(providerId)
        }
    }

    fun selectChatModel(model: String) {
        _uiState.value = _uiState.value.copy(
            chat = _uiState.value.chat.copy(selectedModel = model),
        )
    }

    fun startNewChat() {
        val settings = _uiState.value.settings
        val chat = _uiState.value.chat
        val provider = chat.selectedProviderId
        val model = chat.selectedModel
        if (provider.isNullOrBlank() || model.isNullOrBlank()) {
            _uiState.value = _uiState.value.copy(
                chat = chat.copy(errorMessage = "Pick a provider and model first."),
            )
            return
        }
        viewModelScope.launch {
            _uiState.value = _uiState.value.copy(
                chat = chat.copy(errorMessage = null, transcript = emptyList(), streamingAssistantText = "", streamingThinkingText = ""),
            )
            runCatching {
                val providerKind = chat.providers.firstOrNull { it.id == provider }?.kind ?: "auto"
                repository.createChatSession(
                    settings = settings,
                    model = model,
                    provider = providerKind,
                    label = "Mobile chat",
                )
            }.onSuccess { session ->
                _uiState.value = _uiState.value.copy(
                    chat = _uiState.value.chat.copy(
                        activeSessionId = session.id,
                        activeRunId = null,
                        transcript = emptyList(),
                    ),
                )
            }.onFailure { error ->
                _uiState.value = _uiState.value.copy(
                    chat = _uiState.value.chat.copy(errorMessage = error.message ?: "Could not start chat."),
                )
            }
        }
    }

    fun sendChatMessage(content: String) {
        val text = content.trim()
        if (text.isEmpty()) return
        val settings = _uiState.value.settings
        val sessionId = _uiState.value.chat.activeSessionId
        if (sessionId == null) {
            _uiState.value = _uiState.value.copy(
                chat = _uiState.value.chat.copy(errorMessage = "Start a chat before sending."),
            )
            return
        }
        viewModelScope.launch {
            _uiState.value = _uiState.value.copy(
                chat = _uiState.value.chat.copy(
                    isSendingMessage = true,
                    streamingAssistantText = "",
                    streamingThinkingText = "",
                    errorMessage = null,
                ),
            )
            runCatching { repository.postChatMessage(settings, sessionId, text) }
                .onSuccess { response ->
                    val pendingUserMessage = TranscriptMessageSummary(
                        id = response.message_id,
                        role = "user",
                        content = response.content.ifEmpty { text },
                        position = response.position,
                        token_estimate = 0,
                        created_at = "",
                    )
                    _uiState.value = _uiState.value.copy(
                        chat = _uiState.value.chat.copy(
                            isSendingMessage = false,
                            isStreaming = true,
                            activeRunId = response.run_id,
                            transcript = _uiState.value.chat.transcript + pendingUserMessage,
                        ),
                    )
                    streamActiveRun(sessionId, response.run_id)
                }
                .onFailure { error ->
                    _uiState.value = _uiState.value.copy(
                        chat = _uiState.value.chat.copy(
                            isSendingMessage = false,
                            errorMessage = error.message ?: "Send failed.",
                        ),
                    )
                }
        }
    }

    private fun streamActiveRun(sessionId: String, runId: String) {
        val settings = _uiState.value.settings
        viewModelScope.launch {
            runCatching {
                repository.streamRun(settings, sessionId, runId).collect { event ->
                    handleStreamEvent(sessionId, event)
                }
            }.onFailure { error ->
                MobileLog.e(MobileLog.TAG_NET, "stream.collect.failed", throwable = error)
                _uiState.value = _uiState.value.copy(
                    chat = _uiState.value.chat.copy(
                        isStreaming = false,
                        errorMessage = error.message ?: "Stream failed.",
                    ),
                )
            }
        }
    }

    private fun handleStreamEvent(sessionId: String, event: ChatStreamEvent) {
        val current = _uiState.value.chat
        when (event) {
            is ChatStreamEvent.TextDelta -> {
                _uiState.value = _uiState.value.copy(
                    chat = current.copy(streamingAssistantText = current.streamingAssistantText + event.text),
                )
            }
            is ChatStreamEvent.ThinkingDelta -> {
                _uiState.value = _uiState.value.copy(
                    chat = current.copy(streamingThinkingText = current.streamingThinkingText + event.text),
                )
            }
            is ChatStreamEvent.AssistantFinal -> {
                val finalText = if (event.finalText.isNotBlank()) event.finalText else current.streamingAssistantText
                val newMessage = TranscriptMessageSummary(
                    id = "stream-${System.currentTimeMillis()}",
                    role = "assistant",
                    content = finalText,
                    position = current.transcript.size + 1,
                    token_estimate = 0,
                    created_at = "",
                )
                _uiState.value = _uiState.value.copy(
                    chat = current.copy(
                        transcript = current.transcript + newMessage,
                        streamingAssistantText = "",
                        streamingThinkingText = "",
                    ),
                )
            }
            is ChatStreamEvent.Done -> {
                _uiState.value = _uiState.value.copy(
                    chat = current.copy(
                        isStreaming = false,
                        activeRunId = null,
                    ),
                )
                refreshChatTranscript(sessionId)
            }
            is ChatStreamEvent.Error -> {
                _uiState.value = _uiState.value.copy(
                    chat = current.copy(
                        isStreaming = false,
                        errorMessage = event.message,
                    ),
                )
            }
            ChatStreamEvent.Interrupted -> {
                _uiState.value = _uiState.value.copy(
                    chat = current.copy(isStreaming = false),
                )
            }
            else -> Unit
        }
    }

    private fun refreshChatTranscript(sessionId: String) {
        val settings = _uiState.value.settings
        viewModelScope.launch {
            runCatching { repository.listSessionMessages(settings, sessionId) }
                .onSuccess { messages ->
                    _uiState.value = _uiState.value.copy(
                        chat = _uiState.value.chat.copy(
                            transcript = messages.sortedBy { it.position },
                        ),
                    )
                }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun OpenClosetMobileRoot(viewModel: MainViewModel = viewModel()) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val context = LocalContext.current

    var selectedTab by rememberSaveable { mutableStateOf(0) }
    val tabs = listOf("Chat", "Network", "Progress", "Behavior", "Draft", "Deliveries", "Settings")

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("OpenCloset Mobile") },
                actions = {
                    IconButton(onClick = { viewModel.refresh() }) {
                        Icon(Icons.Outlined.Refresh, contentDescription = "Refresh")
                    }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            TabRow(selectedTabIndex = selectedTab) {
                tabs.forEachIndexed { index, title ->
                    Tab(selected = selectedTab == index, onClick = { selectedTab = index }, text = { Text(title) })
                }
            }

            uiState.error?.let { ErrorBanner(it) }
            uiState.message?.let { MessageBanner(it) }

            when (selectedTab) {
                0 -> ChatTab(uiState = uiState, viewModel = viewModel)
                1 -> NetworkTab(uiState = uiState)
                2 -> ProgressTab(uiState = uiState)
                3 -> BehaviorTab(uiState = uiState, viewModel = viewModel)
                4 -> DraftTab(uiState = uiState, viewModel = viewModel)
                5 -> DeliveriesTab(uiState = uiState, viewModel = viewModel)
                6 -> SettingsTab(uiState = uiState, viewModel = viewModel)
            }
        }
    }
}

@Composable
private fun ErrorBanner(text: String) {
    Text(
        text = text,
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
        color = MaterialTheme.colorScheme.error,
    )
}

@Composable
private fun MessageBanner(text: String) {
    Text(
        text = text,
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
        color = MaterialTheme.colorScheme.primary,
    )
}

@Composable
private fun ChatTab(uiState: MobileUiState, viewModel: MainViewModel) {
    val chat = uiState.chat
    var draft by rememberSaveable { mutableStateOf("") }

    LaunchedEffect(uiState.settings.isConfigured, chat.providers.isEmpty()) {
        if (uiState.settings.isConfigured && chat.providers.isEmpty() && !chat.isLoadingProviders) {
            viewModel.loadChatProviders()
        }
    }

    Column(modifier = Modifier.fillMaxSize()) {
        Card(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 8.dp),
        ) {
            Column(
                modifier = Modifier.padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                    Text("Chat", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                    Spacer(modifier = Modifier.width(8.dp))
                    if (chat.isLoadingProviders) Text("• loading providers…", style = MaterialTheme.typography.bodySmall)
                    Spacer(modifier = Modifier.weight(1f))
                    TextButton(onClick = { viewModel.loadChatProviders() }) { Text("Refresh") }
                }
                Text("Provider", fontWeight = FontWeight.SemiBold)
                if (chat.providers.isEmpty() && !chat.isLoadingProviders) {
                    Text(
                        "No online providers found. Configure providers in OpenCloset, then refresh.",
                        style = MaterialTheme.typography.bodySmall,
                    )
                } else {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        chat.providers.forEach { provider ->
                            val selected = chat.selectedProviderId == provider.id
                            TextButton(onClick = { viewModel.selectChatProvider(provider.id) }) {
                                Text(
                                    text = if (selected) "• ${provider.id} (${provider.kind})" else "${provider.id} (${provider.kind})",
                                    fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                                )
                            }
                        }
                    }
                }
                val models = chat.selectedProviderId?.let { chat.modelsByProvider[it] }.orEmpty()
                Text("Model", fontWeight = FontWeight.SemiBold)
                if (models.isEmpty()) {
                    Text(
                        "No models discovered for this provider yet.",
                        style = MaterialTheme.typography.bodySmall,
                    )
                } else {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        models.forEach { model ->
                            val selected = chat.selectedModel == model
                            TextButton(onClick = { viewModel.selectChatModel(model) }) {
                                Text(
                                    text = if (selected) "• $model" else model,
                                    fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                                )
                            }
                        }
                    }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(
                        onClick = { viewModel.startNewChat() },
                        enabled = chat.selectedProviderId != null && chat.selectedModel != null && !chat.isStreaming,
                    ) {
                        Text(if (chat.activeSessionId == null) "Start chat" else "New chat")
                    }
                    if (chat.activeSessionId != null) {
                        Text(
                            "Session ${chat.activeSessionId.take(8)}…",
                            style = MaterialTheme.typography.bodySmall,
                            modifier = Modifier.padding(top = 12.dp),
                        )
                    }
                }
                chat.errorMessage?.let { msg ->
                    Text(msg, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
                }
            }
        }

        LazyColumn(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth(),
            contentPadding = PaddingValues(horizontal = 12.dp, vertical = 4.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            items(chat.transcript, key = { "${it.id}-${it.position}" }) { msg ->
                ChatBubble(role = msg.role, text = msg.content)
            }
            if (chat.streamingThinkingText.isNotBlank()) {
                item { ChatBubble(role = "thinking", text = chat.streamingThinkingText) }
            }
            if (chat.streamingAssistantText.isNotBlank()) {
                item { ChatBubble(role = "assistant", text = chat.streamingAssistantText, streaming = true) }
            }
            if (chat.isStreaming && chat.streamingAssistantText.isBlank() && chat.streamingThinkingText.isBlank()) {
                item {
                    Text(
                        "Waiting for model…",
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(8.dp),
                    )
                }
            }
        }

        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = draft,
                onValueChange = { draft = it },
                modifier = Modifier.weight(1f),
                placeholder = { Text("Type a message…") },
                enabled = chat.activeSessionId != null && !chat.isStreaming && !chat.isSendingMessage,
            )
            Button(
                onClick = {
                    val text = draft
                    draft = ""
                    viewModel.sendChatMessage(text)
                },
                enabled = chat.activeSessionId != null && draft.isNotBlank() && !chat.isStreaming && !chat.isSendingMessage,
            ) {
                Icon(Icons.AutoMirrored.Outlined.Send, contentDescription = null)
                Spacer(modifier = Modifier.width(4.dp))
                Text(if (chat.isSendingMessage) "Sending" else "Send")
            }
        }
    }
}

@Composable
private fun ChatBubble(role: String, text: String, streaming: Boolean = false) {
    val isUser = role == "user"
    val bubbleColor = when (role) {
        "user" -> MaterialTheme.colorScheme.primary.copy(alpha = 0.18f)
        "assistant" -> MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.4f)
        "thinking" -> MaterialTheme.colorScheme.tertiary.copy(alpha = 0.12f)
        else -> MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.2f)
    }
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start,
    ) {
        Card(
            modifier = Modifier
                .fillMaxWidth(0.92f)
                .padding(vertical = 2.dp),
        ) {
            Column(
                modifier = Modifier
                    .background(bubbleColor)
                    .padding(10.dp),
                verticalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                Text(
                    text = when (role) {
                        "user" -> "You"
                        "assistant" -> if (streaming) "Assistant (streaming)" else "Assistant"
                        "thinking" -> "Thinking"
                        else -> role.replaceFirstChar { it.uppercase() }
                    },
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(text)
            }
        }
    }
}

@Composable
private fun NetworkTab(uiState: MobileUiState) {
    val bootstrap = uiState.bootstrap
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text("Node status", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                    Spacer(modifier = Modifier.height(8.dp))
                    Text("Device: ${uiState.settings.deviceId.ifBlank { "unset" }}")
                    Text("Backend: ${uiState.settings.backendUrl.ifBlank { "unset" }}")
                    Text("Last bootstrap: ${bootstrap?.generated_at ?: "none"}")
                    if (uiState.isRefreshing) {
                        Text("Refreshing network state...")
                    }
                }
            }
        }

        bootstrap?.workspaces?.let { workspaces ->
            items(workspaces, key = { it.id }) { workspace ->
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text(workspace.name, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                        Text(workspace.description.orEmpty())
                        Text("Status: ${workspace.status}  Kind: ${workspace.kind}")
                        val projects = bootstrap.projects_by_workspace[workspace.id].orEmpty()
                        if (projects.isNotEmpty()) {
                            Text("Projects:", fontWeight = FontWeight.SemiBold)
                            projects.forEach { project -> Text("- ${project.name} (${project.status})") }
                        }
                        val deliveries = bootstrap.deliveries_by_workspace[workspace.id].orEmpty()
                        if (deliveries.isNotEmpty()) {
                            Text("Pending deliveries: ${deliveries.count { it.status == "ready" }}")
                        }
                        val sessions = bootstrap.sessions_by_workspace[workspace.id].orEmpty()
                        if (sessions.isNotEmpty()) {
                            Text("Live sessions: ${sessions.size}")
                        }
                    }
                }
            }
        }

        if (!bootstrap?.recent_captures.isNullOrEmpty()) {
            item {
                Text(
                    text = "Recent captures",
                    modifier = Modifier.padding(top = 8.dp, bottom = 4.dp),
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                )
            }
            items(bootstrap?.recent_captures.orEmpty(), key = { it.id }) { capture ->
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text("${capture.event_type} (${capture.status})", fontWeight = FontWeight.SemiBold)
                        Text(capture.content)
                        Text("Source: ${capture.source}")
                    }
                }
            }
        }
    }
}

@Composable
private fun ProgressTab(uiState: MobileUiState) {
    val bootstrap = uiState.bootstrap
    val sessions = bootstrap?.sessions_by_workspace.orEmpty().values.flatten().sortedByDescending { it.updated_at }
    val events = bootstrap?.recent_session_events.orEmpty()

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text("Session relay feed", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                    Text("Mirror active OpenCloset sessions and recent progress events onto the phone node.")
                    Text("Sessions synced: ${sessions.size}")
                    Text("Recent events: ${events.size}")
                }
            }
        }

        if (sessions.isNotEmpty()) {
            item {
                Text("Sessions", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            }
            items(sessions, key = { it.id }) { session ->
                SessionCard(session = session)
            }
        }

        if (events.isNotEmpty()) {
            item {
                Text("Recent progress", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            }
            items(events, key = { it.id }) { event ->
                SessionEventCard(event = event)
            }
        }
    }
}

@Composable
private fun SessionCard(session: MobileSessionSummary) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(session.label.ifBlank { session.id }, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Text("Model: ${session.model} via ${session.provider}")
            Text("Status: ${session.status}  Tokens: ${session.token_count}/${session.context_window}")
            Text("Project: ${session.build_project_id ?: "unscoped"}")
            session.current_run?.let { run ->
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Icon(Icons.Outlined.HourglassBottom, contentDescription = null)
                    Text("Run ${run.turn_number} is ${run.status}")
                }
            }
        }
    }
}

@Composable
private fun SessionEventCard(event: MobileSessionEventSummary) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(event.type, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
            Text("Session: ${event.session_id}")
            Text("Project: ${event.build_project_id ?: "unscoped"}")
            Text(event.data.entries.joinToString(separator = " | ") { "${it.key}=${it.value}" }.ifBlank { "No event payload" })
            Text(event.created_at)
        }
    }
}

@Composable
private fun BehaviorTab(uiState: MobileUiState, viewModel: MainViewModel) {
    val sessions = uiState.bootstrap?.sessions_by_workspace.orEmpty().values.flatten().sortedByDescending { it.updated_at }
    if (sessions.isEmpty()) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Behavior feedback", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Text("No synced sessions are available yet. Refresh first, then rate assistant turns from the phone.")
        }
        return
    }

    var selectedSessionId by rememberSaveable { mutableStateOf(uiState.behavior.sessionId ?: sessions.first().id) }
    val selectedSession = sessions.firstOrNull { it.id == selectedSessionId } ?: sessions.first()
    val assistantMessages = uiState.behavior.messages.filter { it.role == "assistant" }.sortedByDescending { it.position }
    val feedbackByMessageId = uiState.behavior.behaviorState.feedback.associateBy { it.message_id }
    val activePatches = applicableBehaviorPatches(uiState.behavior.behaviorState.patches, selectedSession)
    val proposal = deriveBehaviorProposal(
        entries = uiState.behavior.behaviorState.feedback,
        activePatches = activePatches,
        dismissed = uiState.behavior.behaviorState.dismissals,
        session = selectedSession,
    )
    val scopeOptions = scopeOptionsForSession(selectedSession)
    var selectedScope by rememberSaveable(proposal?.ruleKey) { mutableStateOf(proposal?.recommendedScope ?: scopeOptions.first().scope) }

    LaunchedEffect(selectedSession.id, uiState.settings.isConfigured) {
        if (uiState.settings.isConfigured && uiState.behavior.sessionId != selectedSession.id) {
            viewModel.loadBehaviorSession(selectedSession.id)
        }
    }

    LaunchedEffect(proposal?.ruleKey) {
        if (proposal != null) {
            selectedScope = proposal.recommendedScope
        }
    }

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Behavior feedback", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                    Text("Swipe right to reinforce, swipe left to push back, or promote an assistant turn into a durable behavior patch.")
                    Text("Active session: ${selectedSession.label.ifBlank { selectedSession.id }}")
                    Text("Project: ${selectedSession.build_project_id ?: "unscoped"}")
                    Text("Workspace: ${selectedSession.workspace_id ?: "unscoped"}")
                }
            }
        }

        item {
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Choose session", fontWeight = FontWeight.SemiBold)
                    sessions.take(8).forEach { session ->
                        TextButton(onClick = { selectedSessionId = session.id }) {
                            Text(if (session.id == selectedSessionId) "• ${session.label.ifBlank { session.id }}" else session.label.ifBlank { session.id })
                        }
                    }
                }
            }
        }

        if (activePatches.isNotEmpty()) {
            item {
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("Active behavior patches", fontWeight = FontWeight.SemiBold)
                        activePatches.forEach { patch ->
                            Text("${patch.scope}: ${patch.patch}")
                        }
                    }
                }
            }
        }

        if (proposal != null) {
            item {
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(proposal.title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                        Text(proposal.observedPattern)
                        Text(proposal.hypothesis)
                        Text(proposal.patch)
                        Text("Scope")
                        scopeOptions.forEach { option ->
                            TextButton(onClick = { selectedScope = option.scope }) {
                                Text(if (selectedScope == option.scope) "• ${option.label}" else option.label)
                            }
                        }
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(
                                onClick = {
                                    val option = scopeOptions.firstOrNull { it.scope == selectedScope } ?: scopeOptions.first()
                                    viewModel.applyBehaviorProposal(selectedSession, proposal, option)
                                },
                                enabled = !uiState.behavior.isSaving,
                            ) {
                                Text("Apply")
                            }
                            TextButton(
                                onClick = { viewModel.dismissBehaviorProposal(selectedSession.id, proposal.ruleKey) },
                                enabled = !uiState.behavior.isSaving,
                            ) {
                                Text("Reject")
                            }
                        }
                    }
                }
            }
        }

        if (uiState.behavior.isLoading) {
            item {
                Text("Loading transcript and behavior state…")
            }
        }

        items(assistantMessages, key = { it.id }) { message ->
            AssistantBehaviorCard(
                message = message,
                currentSignal = feedbackByMessageId[message.id]?.signal,
                disabled = uiState.behavior.isSaving,
                onFeedback = { signal -> viewModel.submitBehaviorFeedback(selectedSession, message, signal) },
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AssistantBehaviorCard(
    message: TranscriptMessageSummary,
    currentSignal: String?,
    disabled: Boolean,
    onFeedback: (String) -> Unit,
) {
    val dismissState = rememberSwipeToDismissBoxState(
        confirmValueChange = { value ->
            when (value) {
                SwipeToDismissBoxValue.StartToEnd -> onFeedback("up")
                SwipeToDismissBoxValue.EndToStart -> onFeedback("down")
                SwipeToDismissBoxValue.Settled -> Unit
            }
            false
        },
    )

    SwipeToDismissBox(
        state = dismissState,
        enableDismissFromStartToEnd = !disabled,
        enableDismissFromEndToStart = !disabled,
        backgroundContent = {
            val target = dismissState.targetValue
            val backgroundColor = when (target) {
                SwipeToDismissBoxValue.StartToEnd -> MaterialTheme.colorScheme.primary.copy(alpha = 0.18f)
                SwipeToDismissBoxValue.EndToStart -> MaterialTheme.colorScheme.error.copy(alpha = 0.18f)
                SwipeToDismissBoxValue.Settled -> MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.08f)
            }
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .fillMaxHeight()
                    .background(backgroundColor)
                    .padding(horizontal = 20.dp),
            ) {
                Text(
                    text = when (target) {
                        SwipeToDismissBoxValue.StartToEnd -> "Right swipe: reinforce"
                        SwipeToDismissBoxValue.EndToStart -> "Left swipe: push back"
                        SwipeToDismissBoxValue.Settled -> "Swipe for feedback"
                    },
                    modifier = Modifier.align(
                        if (target == SwipeToDismissBoxValue.EndToStart) androidx.compose.ui.Alignment.CenterEnd
                        else androidx.compose.ui.Alignment.CenterStart,
                    ),
                )
            }
        },
    ) {
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(message.content)
                Text(message.created_at, style = MaterialTheme.typography.bodySmall)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TextButton(onClick = { onFeedback("up") }, enabled = !disabled) {
                        Icon(Icons.Outlined.ThumbUp, contentDescription = null)
                        Spacer(modifier = Modifier.width(4.dp))
                        Text(if (currentSignal == "up") "Right ✓" else "Right")
                    }
                    TextButton(onClick = { onFeedback("down") }, enabled = !disabled) {
                        Icon(Icons.Outlined.ThumbDown, contentDescription = null)
                        Spacer(modifier = Modifier.width(4.dp))
                        Text(if (currentSignal == "down") "Left ✓" else "Left")
                    }
                    TextButton(onClick = { onFeedback("promote") }, enabled = !disabled) {
                        Icon(Icons.Outlined.Star, contentDescription = null)
                        Spacer(modifier = Modifier.width(4.dp))
                        Text(if (currentSignal == "promote") "Promote ✓" else "Promote")
                    }
                }
            }
        }
    }
}

@Composable
private fun DraftTab(uiState: MobileUiState, viewModel: MainViewModel) {
    val bootstrap = uiState.bootstrap
    val workspaces = bootstrap?.workspaces.orEmpty()
    val defaultWorkspaceId = uiState.settings.preferredWorkspaceId.ifBlank { workspaces.firstOrNull()?.id.orEmpty() }
    var workspaceId by rememberSaveable(defaultWorkspaceId) { mutableStateOf(defaultWorkspaceId) }
    var projectId by rememberSaveable { mutableStateOf("") }
    var sessionId by rememberSaveable { mutableStateOf("") }
    var targetDevice by rememberSaveable { mutableStateOf("phone") }
    var why by rememberSaveable { mutableStateOf("") }
    var ramble by rememberSaveable { mutableStateOf("") }
    var distilledBrief by rememberSaveable { mutableStateOf("") }
    var approvalNote by rememberSaveable { mutableStateOf("") }
    var approved by rememberSaveable { mutableStateOf(false) }
    val context = LocalContext.current

    val selectedProjects = bootstrap?.projects_by_workspace?.get(workspaceId).orEmpty()
    val selectedSessions = bootstrap?.sessions_by_workspace?.get(workspaceId).orEmpty()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Feature brief handoff", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        Text("Use PocketPal for local distillation, then submit the approved brief into the OpenCloset network.")

        OutlinedTextField(value = workspaceId, onValueChange = { workspaceId = it }, label = { Text("Workspace ID") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(value = projectId, onValueChange = { projectId = it }, label = { Text("Project ID") }, modifier = Modifier.fillMaxWidth())
        if (selectedProjects.isNotEmpty()) {
            Text("Known projects: ${selectedProjects.joinToString { it.name }}")
        }
        OutlinedTextField(value = sessionId, onValueChange = { sessionId = it }, label = { Text("Session ID (optional route target)") }, modifier = Modifier.fillMaxWidth())
        if (selectedSessions.isNotEmpty()) {
            Text("Known sessions: ${selectedSessions.joinToString { if (it.label.isBlank()) it.id else it.label }}")
        }
        OutlinedTextField(value = targetDevice, onValueChange = { targetDevice = it }, label = { Text("Target device") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(value = why, onValueChange = { why = it }, label = { Text("Why / when / how") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(value = ramble, onValueChange = { ramble = it }, label = { Text("Raw ramble") }, modifier = Modifier.fillMaxWidth(), minLines = 4)
        OutlinedTextField(value = distilledBrief, onValueChange = { distilledBrief = it }, label = { Text("Approved distilled brief") }, modifier = Modifier.fillMaxWidth(), minLines = 4)
        OutlinedTextField(value = approvalNote, onValueChange = { approvalNote = it }, label = { Text("Approval note") }, modifier = Modifier.fillMaxWidth())
        Text(if (sessionId.isBlank()) "Routing mode: workspace/project capture" else "Routing mode: attach to existing session")

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = {
                copyToClipboard(
                    context,
                    label = "PhoneCloset prompt",
                    text = buildPocketPalPrompt(
                        ramble = ramble,
                        workspaceId = workspaceId,
                        projectId = projectId,
                        targetDevice = targetDevice,
                        why = why,
                    ),
                )
                toast(context, "PhoneCloset prompt copied for PocketPal.")
            }) {
                Text("Copy prompt")
            }
            Button(onClick = {
                launchExternalApp(context, uiState.settings.pocketPalPackage)
            }) {
                Text("Open PocketPal")
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TextButton(onClick = { approved = !approved }) {
                Text(if (approved) "Approved for send" else "Mark approved")
            }
            Button(
                onClick = {
                    viewModel.submitFeatureDraft(
                        workspaceId = workspaceId,
                        projectId = projectId.ifBlank { null },
                        sessionId = sessionId.ifBlank { null },
                        ramble = ramble,
                        distilledBrief = distilledBrief,
                        targetDevice = targetDevice,
                        why = why,
                        approvalNote = approvalNote,
                    )
                },
                enabled = approved && workspaceId.isNotBlank() && distilledBrief.isNotBlank() && !uiState.isSubmitting,
            ) {
                Icon(Icons.AutoMirrored.Outlined.Send, contentDescription = null)
                Spacer(modifier = Modifier.height(0.dp))
                Text("Send to OpenCloset")
            }
        }
    }
}

@Composable
private fun DeliveriesTab(uiState: MobileUiState, viewModel: MainViewModel) {
    val context = LocalContext.current
    val settings = uiState.settings
    val deliveries = uiState.bootstrap?.deliveries_by_workspace.orEmpty()
        .values
        .flatten()
        .sortedByDescending { it.created_at }

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        if (deliveries.isEmpty()) {
            item {
                Text("No deliveries queued for this device.")
            }
        }
        items(deliveries, key = { it.id }) { delivery ->
            DeliveryCard(
                delivery = delivery,
                backendUrl = settings.backendUrl,
                onDownload = {
                    openUrl(context, settings.backendUrl + delivery.download_url.removePrefix("/"))
                },
                onMarkDownloaded = {
                    viewModel.acknowledgeDelivery(delivery.workspace_id, delivery.id, "downloaded", "Downloaded on phone")
                },
                onMarkInstalled = {
                    viewModel.acknowledgeDelivery(delivery.workspace_id, delivery.id, "installed", "Installed on phone")
                },
                onMarkFailed = {
                    viewModel.acknowledgeDelivery(delivery.workspace_id, delivery.id, "failed", "Install or download failed")
                },
            )
        }
    }
}

@Composable
private fun DeliveryCard(
    delivery: ProjectDeliverySummary,
    backendUrl: String,
    onDownload: () -> Unit,
    onMarkDownloaded: () -> Unit,
    onMarkInstalled: () -> Unit,
    onMarkFailed: () -> Unit,
) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(delivery.file_name, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Text("Kind: ${delivery.artifact_kind}  Status: ${delivery.status}")
            Text("Project: ${delivery.build_project_id}")
            Text("Target: ${delivery.target_device_id ?: "broadcast"}")
            Text("Backend: $backendUrl")
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = onDownload) {
                    Icon(Icons.Outlined.Download, contentDescription = null)
                    Text("Download")
                }
                TextButton(onClick = onMarkDownloaded) { Text("Mark downloaded") }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TextButton(onClick = onMarkInstalled) { Text("Mark installed") }
                TextButton(onClick = onMarkFailed) { Text("Mark failed") }
            }
        }
    }
}

@Composable
private fun SettingsTab(uiState: MobileUiState, viewModel: MainViewModel) {
    var backendUrl by rememberSaveable(uiState.settings.backendUrl) { mutableStateOf(uiState.settings.backendUrl) }
    var deviceId by rememberSaveable(uiState.settings.deviceId) { mutableStateOf(uiState.settings.deviceId) }
    var preferredWorkspaceId by rememberSaveable(uiState.settings.preferredWorkspaceId) { mutableStateOf(uiState.settings.preferredWorkspaceId) }
    var pocketPalPackage by rememberSaveable(uiState.settings.pocketPalPackage) { mutableStateOf(uiState.settings.pocketPalPackage) }
    val context = LocalContext.current

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Node settings", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        OutlinedTextField(value = backendUrl, onValueChange = { backendUrl = it }, label = { Text("Backend URL") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(value = deviceId, onValueChange = { deviceId = it }, label = { Text("Device ID") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(value = preferredWorkspaceId, onValueChange = { preferredWorkspaceId = it }, label = { Text("Preferred workspace ID") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(value = pocketPalPackage, onValueChange = { pocketPalPackage = it }, label = { Text("PocketPal package name") }, modifier = Modifier.fillMaxWidth())
        Button(onClick = {
            viewModel.saveSettings(backendUrl, deviceId, preferredWorkspaceId, pocketPalPackage)
            viewModel.refresh()
        }) {
            Text("Save and sync")
        }

        Spacer(modifier = Modifier.height(8.dp))
        Text("Debug logs", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        Text("Export the most recent in-app log lines if the app crashes before reaching this screen on the next start.")
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = {
                val logs = MobileLog.readRecentLines(maxLines = 200)
                if (logs.isBlank()) {
                    toast(context, "No logs captured yet.")
                } else {
                    copyToClipboard(context, "OpenCloset logs", logs)
                    toast(context, "Copied last log lines to clipboard.")
                }
            }) {
                Text("Copy recent logs")
            }
            Button(onClick = {
                val logs = MobileLog.readRecentLines(maxLines = 500)
                if (logs.isBlank()) {
                    toast(context, "No logs captured yet.")
                } else {
                    val intent = Intent(Intent.ACTION_SEND).apply {
                        type = "text/plain"
                        putExtra(Intent.EXTRA_SUBJECT, "OpenCloset Mobile log export")
                        putExtra(Intent.EXTRA_TEXT, logs)
                    }
                    context.startActivity(Intent.createChooser(intent, "Share OpenCloset logs"))
                }
            }) {
                Text("Share logs")
            }
        }
        TextButton(onClick = {
            MobileLog.clear()
            toast(context, "Logs cleared.")
        }) {
            Text("Clear logs")
        }
    }
}

private fun buildPocketPalPrompt(
    ramble: String,
    workspaceId: String,
    projectId: String,
    targetDevice: String,
    why: String,
): String {
    return """
You are PhoneCloset, the local OpenCloset phone node.

Distill the ramble below into an approval-ready feature brief.

Return:
- feature name
- concise summary
- target device
- destination workspace id
- destination build project id
- destination session id if this should land on an existing thread
- why / when / how
- open questions that must be confirmed before sending to the wider OpenCloset network

Known routing hints:
- workspace id: $workspaceId
- build project id: ${projectId.ifBlank { "unknown" }}
- target device: $targetDevice
- why/when/how hints: $why

Ramble:
$ramble
    """.trimIndent()
}

private fun copyToClipboard(context: Context, label: String, text: String) {
    val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    clipboard.setPrimaryClip(ClipData.newPlainText(label, text))
}

private fun launchExternalApp(context: Context, packageName: String) {
    if (packageName.isBlank()) {
        toast(context, "Set the PocketPal package name in Settings first.")
        return
    }
    val launchIntent = context.packageManager.getLaunchIntentForPackage(packageName)
    if (launchIntent == null) {
        toast(context, "PocketPal package not found on this phone.")
        return
    }
    context.startActivity(launchIntent)
}

private fun openUrl(context: Context, url: String) {
    context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
}

private fun toast(context: Context, message: String) {
    Toast.makeText(context, message, Toast.LENGTH_SHORT).show()
}

private fun truncateBehaviorText(value: String, limit: Int = 120): String {
    val normalized = value.replace("\n", " ").replace(Regex("\\s+"), " ").trim()
    return if (normalized.length <= limit) normalized else normalized.take(limit - 3) + "..."
}

private fun classifyAssistantTraits(value: String): List<String> {
    val lower = value.lowercase()
    val traits = linkedSetOf<String>()
    val nonBlankLines = value.lines().count { it.isNotBlank() }
    val hasCode = Regex("```|`[^`]+`").containsMatchIn(value)
    val hasFileSignal = Regex("\\b(?:patch|diff|test|build|validate|verification|run|grep|search|read_file|apply_patch|error|failure mode|next patch target|line\\s+\\d+|\\.tsx?\\b|\\.py\\b|\\.md\\b|\\.json\\b)\\b").containsMatchIn(lower)
    val preservesExisting = Regex("\\b(?:existing|already exists|preserve|extend|inspect|patch it|edit it|current state)\\b").containsMatchIn(lower)
    val rewriteRisk = Regex("\\b(?:from scratch|rewrite|recreate|greenfield|start over)\\b").containsMatchIn(lower)
    val abstractSignal = Regex("\\b(?:architecture|conceptual|conceptually|overall|directionally|framework|worldview|product direction)\\b").containsMatchIn(lower)

    if (hasCode || hasFileSignal) traits += "actionable"
    if (preservesExisting) traits += "preserve_existing"
    if (rewriteRisk || (value.length > 420 && !hasCode && !hasFileSignal && !preservesExisting)) traits += "rewrite_risk"
    if (value.length <= 220 && nonBlankLines <= 6) traits += "compact"
    if (abstractSignal || ((value.length > 360 || nonBlankLines >= 7) && !hasCode && !hasFileSignal)) traits += "abstract"
    if (traits.isEmpty()) traits += "compact"

    return traits.toList()
}

private fun scopeOptionsForSession(session: MobileSessionSummary): List<BehaviorScopeOption> {
    val options = mutableListOf(BehaviorScopeOption(scope = "chat", label = "This chat", scopeId = session.id))
    session.build_project_id?.takeIf { it.isNotBlank() }?.let {
        options += BehaviorScopeOption(scope = "build_project", label = "Build project", scopeId = it)
    }
    session.workspace_id?.takeIf { it.isNotBlank() }?.let {
        options += BehaviorScopeOption(scope = "workspace", label = "Workspace", scopeId = it)
    }
    options += BehaviorScopeOption(scope = "global", label = "Global", scopeId = "global")
    return options
}

private fun applicableBehaviorPatches(
    patches: List<BehaviorPatchRecord>,
    session: MobileSessionSummary,
): List<BehaviorPatchRecord> {
    return patches.filter { patch ->
        when (patch.scope) {
            "global" -> patch.scope_id == "global"
            "chat" -> patch.scope_id == session.id
            "build_project" -> session.build_project_id != null && patch.scope_id == session.build_project_id
            "workspace" -> session.workspace_id != null && patch.scope_id == session.workspace_id
            else -> false
        }
    }
}

private fun topTrait(entries: List<BehaviorFeedbackRecord>, signal: String): Pair<String, Int>? {
    val counts = linkedMapOf<String, Int>()
    entries.filter { it.signal == signal }.forEach { entry ->
        entry.traits.forEach { trait ->
            counts[trait] = (counts[trait] ?: 0) + 1
        }
    }
    return counts.maxByOrNull { it.value }?.toPair()
}

private fun describeTrait(trait: String?): String {
    return when (trait) {
        "actionable" -> "concrete, file-aware execution"
        "abstract" -> "abstract framing before action"
        "preserve_existing" -> "inspect-and-patch behavior"
        "rewrite_risk" -> "rewrite-from-scratch drift"
        "compact" -> "tight, low-friction delivery"
        else -> "recent behavior"
    }
}

private fun deriveBehaviorProposal(
    entries: List<BehaviorFeedbackRecord>,
    activePatches: List<BehaviorPatchRecord>,
    dismissed: List<BehaviorDismissalRecord>,
    session: MobileSessionSummary,
): MobileBehaviorProposal? {
    val candidateEntries = listOf(
        session.build_project_id?.let { buildProjectId -> entries.filter { it.build_project_id == buildProjectId } } ?: emptyList(),
        session.workspace_id?.let { workspaceId -> entries.filter { it.workspace_id == workspaceId } } ?: emptyList(),
        entries.filter { it.session_id == session.id },
    )

    val relevantEntries = (candidateEntries.firstOrNull { it.size >= 4 } ?: candidateEntries.last())
        .sortedByDescending { it.created_at }
        .take(12)
    if (relevantEntries.size < 4) {
        return null
    }

    val positiveTop = topTrait(relevantEntries, "up") ?: topTrait(relevantEntries, "promote")
    val negativeTop = topTrait(relevantEntries, "down")
    if (positiveTop == null && negativeTop == null) {
        return null
    }

    val appliedRuleKeys = activePatches.map { it.rule_key }.toSet()
    val dismissedRuleKeys = dismissed.filter { it.session_id == session.id }.map { it.rule_key }.toSet()
    val sampleCount = relevantEntries.size

    fun maybeReturn(proposal: MobileBehaviorProposal?): MobileBehaviorProposal? {
        if (proposal == null) return null
        if (appliedRuleKeys.contains(proposal.ruleKey) || dismissedRuleKeys.contains(proposal.ruleKey)) return null
        return proposal
    }

    if ((positiveTop?.first == "preserve_existing" && (negativeTop?.first == "rewrite_risk" || negativeTop?.first == "abstract")) || negativeTop?.first == "rewrite_risk") {
        return maybeReturn(
            MobileBehaviorProposal(
                ruleKey = "preserve-existing-artifacts",
                title = "I think I learned something from your recent feedback.",
                observedPattern = "You reinforce ${describeTrait(positiveTop?.first ?: "preserve_existing")} and push back on ${describeTrait(negativeTop?.first ?: "rewrite_risk")}.",
                hypothesis = "Across the last $sampleCount rated assistant turns, preserving existing artifacts reads as higher trust than broad rewrites.",
                patch = "When a file or artifact already exists, inspect it first, summarize whether it is being edited, extended, or replaced, and avoid recreating it from scratch unless explicitly requested.",
                recommendedScope = when {
                    !session.build_project_id.isNullOrBlank() -> "build_project"
                    !session.workspace_id.isNullOrBlank() -> "workspace"
                    else -> "chat"
                },
                sampleCount = sampleCount,
            )
        )
    }

    if (negativeTop?.first == "abstract" && (positiveTop?.first == "actionable" || positiveTop?.first == "compact")) {
        return maybeReturn(
            MobileBehaviorProposal(
                ruleKey = "lead-with-failure-and-target",
                title = "I think I learned something from your recent feedback.",
                observedPattern = "You reinforce ${describeTrait(positiveTop?.first ?: "actionable")} and push back on ${describeTrait("abstract")}.",
                hypothesis = "Across the last $sampleCount rated assistant turns, direct execution framing lands better than conceptual setup.",
                patch = "For runtime bugs and build work, lead with the exact failure mode and the next patch target before broader framing. After stating intent, move directly to a tool action, diff, or validation result.",
                recommendedScope = if (!session.workspace_id.isNullOrBlank()) "workspace" else "chat",
                sampleCount = sampleCount,
            )
        )
    }

    if (positiveTop?.first == "compact" && negativeTop != null) {
        return maybeReturn(
            MobileBehaviorProposal(
                ruleKey = "compress-pre-action-narration",
                title = "I think I learned something from your recent feedback.",
                observedPattern = "You reinforce ${describeTrait("compact")} and push back on ${describeTrait(negativeTop.first)}.",
                hypothesis = "Across the last $sampleCount rated assistant turns, shorter lead-ins are earning more trust than extended narration.",
                patch = "Keep pre-action narration brief. Once the next step is stated, the next visible event should be a concrete action, a code change, a command result, or an explicit blocker.",
                recommendedScope = "chat",
                sampleCount = sampleCount,
            )
        )
    }

    return null
}