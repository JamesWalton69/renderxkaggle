package com.kaggle.manager.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilterChip
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
import com.kaggle.manager.Api.Job
import kotlinx.coroutines.launch

private val FILTERS = listOf<String?>(null, "queued", "running", "stopping", "success", "failed", "cancelled", "interrupted", "uncertain")

@Composable
fun JobsTab() {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    var filter by remember { mutableStateOf<String?>(null) }
    var jobs by remember { mutableStateOf<List<Job>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var detailJob by remember { mutableStateOf<Job?>(null) }

    fun refresh() {
        scope.launch {
            try {
                jobs = Api.jobs(filter)
                error = null
            } catch (e: Exception) {
                error = e.message
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(filter) { refresh() }
    rememberAutoRefresh { refresh() }

    Column(Modifier.fillMaxSize()) {
        LazyRow(contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            items(FILTERS) { f ->
                FilterChip(
                    selected = filter == f,
                    onClick = { filter = f },
                    label = { Text(f ?: "all", fontSize = 12.sp) },
                )
            }
        }
        Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Jobs", style = MaterialTheme.typography.titleLarge)
                Spacer(Modifier.weight(1f))
                TextButton(onClick = { refresh() }) { Text("↻", color = Muted) }
            }
            error?.let { ErrorBox(it) { error = null; refresh() } }
            if (jobs.isEmpty() && !loading) {
                Empty("No ${filter ?: ""} jobs")
            } else {
                LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    items(jobs, key = { it.id }) { job ->
                        JobCard(job, onClick = { detailJob = job })
                    }
                }
            }
        }
    }

    detailJob?.let { job ->
        JobDetailDialog(
            jobId = job.id,
            onDismiss = { detailJob = null },
            onChanged = {
                detailJob = null
                refresh()
            },
            onError = { Toast.makeText(context, it, Toast.LENGTH_LONG).show() },
        )
    }
}

@Composable
private fun JobCard(job: Job, onClick: () -> Unit) {
    Card(
        onClick = onClick,
        colors = CardDefaults.cardColors(containerColor = Surface),
        shape = RoundedCornerShape(12.dp),
    ) {
        Column(Modifier.fillMaxWidth().padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("#${job.id} · ${job.notebookTitle ?: "deleted notebook"}", fontWeight = FontWeight.SemiBold, fontSize = 13.5sp)
                Spacer(Modifier.weight(1f))
                StatusBadge(job.status)
            }
            SpacerH(6)
            Text(
                "${job.kind} · kaggle ${job.kaggleStatus ?: "—"} · ${fmtTime(job.createdAt)} · ${duration(job.startedAt, job.finishedAt)}",
                color = Muted,
                fontSize = 11.5sp,
            )
        }
    }
}

@Composable
private fun JobDetailDialog(jobId: Int, onDismiss: () -> Unit, onChanged: () -> Unit, onError: (String) -> Unit) {
    val scope = rememberCoroutineScope()
    var detail by remember { mutableStateOf<Api.JobDetail?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var confirm by remember { mutableStateOf<Pair<String, String>?>(null) } // (action, message)
    var busy by remember { mutableStateOf(false) }

    LaunchedEffect(jobId) {
        try {
            detail = Api.job(jobId)
        } catch (e: Exception) {
            error = e.message
        }
    }

    val job = detail?.job
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Job #$jobId") },
        text = {
            Column {
                job?.let { j ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        StatusBadge(j.status)
                        SpacerW(8)
                        if (j.kaggleStatus != null) Text("kaggle: ${j.kaggleStatus}", color = Muted, fontSize = 12.sp)
                    }
                    SpacerH(8)
                    KeyValueRow("notebook", j.notebookTitle ?: "deleted")
                    KeyValueRow("kind", j.kind)
                    KeyValueRow("created", fmtTime(j.createdAt))
                    KeyValueRow("duration", duration(j.startedAt, j.finishedAt))
                    j.error?.let { Text("⚠ $it", color = Red, fontSize = 12.5sp, modifier = Modifier.padding(top = 8.dp)) }
                    SpacerH(10)
                    Text("Logs", color = Muted, fontSize = 12.sp)
                    Box(
                        Modifier.fillMaxWidth().heightIn(max = 280.dp)
                            .background(Ink, RoundedCornerShape(10.dp)).padding(10.dp),
                    ) {
                        val logs = detail?.logs.orEmpty()
                        if (logs.isEmpty()) {
                            Text("No log lines yet.", color = Muted, fontSize = 12.sp)
                        } else {
                            LazyColumn {
                                items(logs) { (ts, line) ->
                                    Text("[${ts.takeLast(8)}] $line", color = Muted, fontSize = 11.5sp)
                                }
                            }
                        }
                    }
                } ?: error?.let { Text(it, color = Red, fontSize = 13.sp) }
            }
        },
        confirmButton = {
            Row {
                val j = job
                if (j != null) {
                    when (j.status) {
                        "queued", "uncertain" -> ActionButton("Cancel", true) {
                            confirm = "Cancel" to "Cancel job #${j.id}? (Queued/uncertain only.)"
                        }
                        "running", "stopping" -> ActionButton("Stop", true) {
                            confirm = "Stop" to "Stop job #${j.id}? Outputs are snapshotted first, then an immediate-exit stub is pushed to Kaggle."
                        }
                        else -> ActionButton("Requeue", false) {
                            confirm = "Requeue" to "Requeue job #${j.id}? It will run again when the queue is free."
                        }
                    }
                }
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Close") } },
    )

    confirm?.let { (action, msg) ->
        ConfirmDialog(
            title = "$action job",
            message = msg,
            confirmLabel = action,
            danger = action != "Requeue",
            onConfirm = {
                busy = true
                scope.launch {
                    try {
                        when (action) {
                            "Cancel", "Stop" -> Api.cancelJob(jobId)
                            else -> Api.requeueJob(jobId)
                        }
                        onChanged()
                    } catch (e: Exception) {
                        onError(e.message ?: "failed")
                        busy = false
                    }
                }
            },
            onDismiss = { confirm = null },
        )
    }
}

@Composable
private fun ActionButton(label: String, danger: Boolean, onClick: () -> Unit) {
    TextButton(onClick = onClick) {
        Text(label, color = if (danger) Red else Sky)
    }
}
