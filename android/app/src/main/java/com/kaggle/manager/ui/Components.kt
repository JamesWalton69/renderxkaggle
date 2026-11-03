package com.kaggle.manager.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay

fun statusColors(status: String?): Pair<Color, Color> = when (status) {
    "queued" -> Color(0x21F59E0B) to Amber
    "running" -> Color(0x2138BDF8) to Sky
    "stopping" -> Color(0x21FB923C) to Color(0xFFFDBA74)
    "success" -> Color(0x2110B981) to Green
    "failed" -> Color(0x21F43F5E) to Red
    "cancelled" -> Color(0x2194A3B8) to Muted
    "interrupted" -> Color(0x21F59E0B) to Color(0xFFFCD34D)
    "uncertain" -> Color(0x21A78BFA) to Violet
    else -> Color(0x2194A3B8) to Muted
}

@Composable
fun StatusBadge(status: String?) {
    val (bg, fg) = statusColors(status)
    Text(
        text = status ?: "—",
        color = fg,
        fontSize = 11.sp,
        fontWeight = FontWeight.SemiBold,
        maxLines = 1,
        modifier = Modifier
            .background(bg, RoundedCornerShape(999.dp))
            .padding(horizontal = 8.dp, vertical = 3.dp),
    )
}

@Composable
fun Empty(text: String) {
    Box(Modifier.fillMaxSize().padding(32.dp), contentAlignment = Alignment.Center) {
        Text(text, color = Muted, fontSize = 14.sp)
    }
}

@Composable
fun ErrorBox(message: String, onRetry: () -> Unit) {
    Column(
        Modifier.fillMaxWidth().background(Color(0x21F43F5E), RoundedCornerShape(10.dp))
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text(message, color = Red, fontSize = 13.sp)
        TextButton(onClick = onRetry) { Text("Retry", color = Red) }
    }
}

@Composable
fun ConfirmDialog(
    title: String,
    message: String,
    confirmLabel: String = "Confirm",
    danger: Boolean = true,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { Text(message) },
        confirmButton = {
            TextButton(onClick = { onConfirm(); onDismiss() }) {
                Text(confirmLabel, color = if (danger) Red else Sky)
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

private val FMT = java.time.format.DateTimeFormatter.ofPattern("MMM d HH:mm")

fun fmtTime(iso: String?): String {
    if (iso.isNullOrBlank()) return "—"
    return runCatching { java.time.OffsetDateTime.parse(iso).format(FMT) }
        .getOrDefault("—")
}

fun duration(start: String?, end: String?): String {
    if (start.isNullOrBlank() || end.isNullOrBlank()) return "—"
    return runCatching {
        val s = java.time.OffsetDateTime.parse(start)
        val e = java.time.OffsetDateTime.parse(end)
        val sec = java.time.Duration.between(s, e).seconds.coerceAtLeast(0)
        when {
            sec >= 3600 -> "${sec / 3600}h ${(sec % 3600) / 60}m"
            sec >= 60 -> "${sec / 60}m ${sec % 60}s"
            else -> "${sec}s"
        }
    }.getOrDefault("—")
}

@Composable
fun SmallSpinner() {
    CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
}

/** Re-launches `refresh` every intervalMs while this composable is composed. */
@Composable
fun rememberAutoRefresh(intervalMs: Long = 30_000, refresh: () -> Unit) {
    LaunchedEffect(Unit) {
        while (true) {
            delay(intervalMs)
            refresh()
        }
    }
}

@Composable
fun KeyValueRow(key: String, value: String) {
    Row(Modifier.fillMaxWidth()) {
        Text(key, color = Muted, fontSize = 12.5.sp, modifier = Modifier.width(92.dp))
        Text(value, color = androidx.compose.material3.MaterialTheme.colorScheme.onSurface, fontSize = 12.5.sp)
    }
}

@Composable
fun SpacerW(w: Int) {
    Spacer(Modifier.width(w.dp))
}

@Composable
fun SpacerH(h: Int) {
    Spacer(Modifier.height(h.dp))
}
