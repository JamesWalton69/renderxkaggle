package com.kaggle.manager.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.kaggle.manager.Api
import com.kaggle.manager.Api.Job
import com.kaggle.manager.Api.OverviewData
import kotlinx.coroutines.launch

@Composable
fun OverviewTab() {
    val scope = rememberCoroutineScope()
    var data by remember { mutableStateOf<OverviewData?>(null) }
    var error by remember { mutableStateOf<String?>(null) }

    fun refresh() {
        scope.launch {
            try {
                data = Api.overview()
                error = null
            } catch (e: Exception) {
                error = e.message
            }
        }
    }

    LaunchedEffect(Unit) { refresh() }
    rememberAutoRefresh { refresh() }

    val d = data
    if (d == null && error == null) {
        androidx.compose.foundation.layout.Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            SmallSpinner()
        }
        return
    }

    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Overview", style = MaterialTheme.typography.titleLarge)
            Spacer(Modifier.weight(1f))
            TextButtonSmall("↻") { refresh() }
        }

        error?.let {
            SpacerH(8)
            ErrorBox(it) { error = null; refresh() }
        }

        d?.let { ov ->
            // stat chips
            Row(Modifier.fillMaxWidth().padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                StatChip("engine", ov.engineState)
                StatChip("jobs", ov.totalJobs.toString())
                StatChip("ok", ov.successRate?.let { "${it.toInt()}%" } ?: "—")
                StatChip("nbs", ov.notebookCount.toString())
                StatChip("approvals", ov.pendingApprovals.toString())
            }

            // active job card (prefer newest running from recent; fall back to engine active)
            val active = ov.recentJobs.firstOrNull { it.status == "running" }
                ?: ov.recentJobs.firstOrNull { it.status == "queued" }
                ?: ov.activeJob
            if (active != null) {
                SpacerH(14)
                Card(
                    colors = CardDefaults.cardColors(containerColor = Surface),
                    shape = RoundedCornerShape(14.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Column(Modifier.padding(14.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("Active job", color = Muted, fontSize = 12.sp)
                            Spacer(Modifier.weight(1f))
                            StatusBadge(active.status)
                        }
                        SpacerH(6)
                        Text(active.notebookTitle ?: "deleted notebook", fontWeight = FontWeight.SemiBold)
                        Text("#${active.id} · ${active.kind} · ${active.kaggleStatus ?: "—"}", color = Muted, fontSize = 12.sp)
                    }
                }
            }

            SpacerH(16)
            Text("Recent jobs", color = Muted, fontSize = 12.sp)
            SpacerH(6)
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(ov.recentJobs.take(8), key = { it.id }) { job ->
                    RecentJobRow(job)
                }
            }
        }
    }
}

@Composable
private fun StatChip(label: String, value: String) {
    Column(
        Modifier.background(Color(0x14FFFFFF), RoundedCornerShape(12.dp)).padding(horizontal = 12.dp, vertical = 8.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(value, color = Sky, fontWeight = FontWeight.Bold, fontSize = 16.sp)
        Text(label, color = Muted, fontSize = 10.sp)
    }
}

@Composable
private fun RecentJobRow(job: Job) {
    Card(colors = CardDefaults.cardColors(containerColor = Surface), shape = RoundedCornerShape(12.dp)) {
        Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(job.notebookTitle ?: "deleted notebook", fontSize = 13.5.sp, fontWeight = FontWeight.SemiBold)
                Text("#${job.id} · ${fmtTime(job.createdAt)} · ${duration(job.startedAt, job.finishedAt)}", color = Muted, fontSize = 11.5.sp)
            }
            StatusBadge(job.status)
        }
    }
}

@Composable
private fun TextButtonSmall(label: String, onClick: () -> Unit) {
    androidx.compose.material3.TextButton(onClick = onClick) { Text(label, color = Muted) }
}
