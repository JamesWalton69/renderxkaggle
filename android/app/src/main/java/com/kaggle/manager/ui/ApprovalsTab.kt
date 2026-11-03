package com.kaggle.manager.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import android.widget.Toast
import com.kaggle.manager.Api
import com.kaggle.manager.Api.Approval
import kotlinx.coroutines.launch

@Composable
fun ApprovalsTab() {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    var approvals by remember { mutableStateOf<List<Approval>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var busyId by remember { mutableStateOf<Int?>(null) }

    fun refresh() {
        scope.launch {
            try {
                approvals = Api.approvals()
                error = null
            } catch (e: Exception) {
                error = e.message
            } finally {
                loading = false
            }
        }
    }

    fun decide(id: Int, approve: Boolean) {
        if (busyId != null) return
        busyId = id
        scope.launch {
            try {
                Api.decideApproval(id, approve)
                Toast.makeText(context, if (approve) "Approved" else "Rejected", Toast.LENGTH_SHORT).show()
                refresh()
            } catch (e: Exception) {
                Toast.makeText(context, e.message, Toast.LENGTH_LONG).show()
            } finally {
                busyId = null
            }
        }
    }

    LaunchedEffect(Unit) { refresh() }
    rememberAutoRefresh { refresh() }

    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Approvals", style = MaterialTheme.typography.titleLarge)
            Spacer(Modifier.weight(1f))
            TextButton(onClick = { refresh() }) { Text("↻", color = Muted) }
        }
        error?.let { ErrorBox(it) { error = null; refresh() } }

        if (approvals.isEmpty() && !loading) {
            Empty("No pending approvals 🎉")
        } else {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                items(approvals, key = { it.id }) { a ->
                    ApprovalCard(
                        a = a,
                        busy = busyId == a.id,
                        onApprove = { decide(a.id, true) },
                        onReject = { decide(a.id, false) },
                    )
                }
            }
        }
    }
}

@Composable
private fun ApprovalCard(a: Approval, busy: Boolean, onApprove: () -> Unit, onReject: () -> Unit) {
    Card(colors = CardDefaults.cardColors(containerColor = Surface), shape = RoundedCornerShape(14.dp)) {
        Column(Modifier.fillMaxWidth().padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("#${a.id} · ${a.tool}", fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                Spacer(Modifier.weight(1f))
                if (busy) SmallSpinner()
            }
            SpacerH(6)
            Text(a.reason, fontSize = 13.sp)
            Text(a.params, color = Muted, fontSize = 11.sp, maxLines = 3)
            SpacerH(8)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TextButton(onClick = onApprove, enabled = !busy) { Text("Approve", color = Green) }
                TextButton(onClick = onReject, enabled = !busy) { Text("Reject", color = Red) }
                Spacer(Modifier.weight(1f))
                Text(fmtTime(a.createdAt), color = Muted, fontSize = 11.sp, modifier = Modifier.align(Alignment.CenterVertically))
            }
        }
    }
}
