package com.kaggle.manager.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.kaggle.manager.Api
import com.kaggle.manager.Api.ChatMsg
import kotlinx.coroutines.launch

@Composable
fun ChatTab(onOpenApprovals: () -> Unit) {
    val scope = rememberCoroutineScope()
    var messages by remember { mutableStateOf<List<ChatMsg>>(emptyList()) }
    var input by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var loaded by remember { mutableStateOf(false) }
    val listState = rememberLazyListState()

    LaunchedEffect(Unit) {
        if (!loaded) {
            messages = runCatching { Api.history() }.getOrDefault(emptyList())
            loaded = true
        }
        listState.scrollToItem(messages.size.coerceAtLeast(0))
    }

    fun send() {
        val text = input.trim()
        if (text.isEmpty() || busy) return
        busy = true
        val userMsg = ChatMsg("user", text, "")
        messages = messages + userMsg
        input = ""
        scope.launch {
            try {
                val reply = Api.chat(text)
                val updates = mutableListOf<ChatMsg>()
                reply.answer?.let { updates += ChatMsg("assistant", it, "") }
                reply.pendingApproval?.let {
                    updates += ChatMsg("note", "⚠ Approval required for this action — see Approvals.", "")
                }
                reply.message?.let { updates += ChatMsg("note", it, "") }
                messages = messages + updates
            } catch (e: Exception) {
                messages = messages + ChatMsg("note", "Error: ${e.message}", "")
            } finally {
                busy = false
            }
        }
    }

    Column(Modifier.fillMaxSize()) {
        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).fillMaxWidth(),
            contentPadding = PaddingValues(14.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(messages) { m -> MessageBubble(m, onOpenApprovals) }
            if (busy) {
                item {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        SmallSpinner()
                        Spacer(Modifier.width(8.dp))
                        Text("assistant is thinking…", color = Muted, fontSize = 12.sp)
                    }
                }
            }
        }

        Row(Modifier.fillMaxWidth().padding(10.dp), verticalAlignment = Alignment.Bottom) {
            OutlinedTextField(
                value = input,
                onValueChange = { input = it },
                placeholder = { Text("Message the assistant…", fontSize = 13.sp) },
                maxLines = 3,
                modifier = Modifier.weight(1f),
            )
            Spacer(Modifier.width(8.dp))
            Button(onClick = { send() }, enabled = input.isNotBlank() && !busy) {
                Text("➤", fontSize = 15.sp)
            }
        }
    }
}

@Composable
private fun MessageBubble(m: ChatMsg, onOpenApprovals: () -> Unit) {
    when (m.role) {
        "user" -> Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
            Text(
                m.content,
                color = Ink,
                fontSize = 13.5sp,
                modifier = Modifier.background(Sky, RoundedCornerShape(14.dp, 14.dp, 4.dp, 14.dp))
                    .padding(horizontal = 14.dp, vertical = 10.dp),
            )
        }
        "assistant" -> Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterStart) {
            Text(
                m.content,
                color = androidx.compose.material3.MaterialTheme.colorScheme.onSurface,
                fontSize = 13.5sp,
                modifier = Modifier.background(Surface, RoundedCornerShape(14.dp, 14.dp, 14.dp, 4.dp))
                    .padding(horizontal = 14.dp, vertical = 10.dp),
            )
        }
        else -> {
            Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(m.content, color = Amber, fontSize = 12.5sp, fontStyle = FontStyle.Italic)
                if (m.content.contains("Approvals")) {
                    TextButton(onClick = onOpenApprovals) { Text("Open", color = Sky, fontSize = 12.sp) }
                }
            }
        }
    }
}
