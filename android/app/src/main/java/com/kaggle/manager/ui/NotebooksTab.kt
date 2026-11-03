package com.kaggle.manager.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import android.widget.Toast
import com.kaggle.manager.Api
import com.kaggle.manager.Api.Notebook
import kotlinx.coroutines.launch

@Composable
fun NotebooksTab() {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    var notebooks by remember { mutableStateOf<List<Notebook>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var showCreate by remember { mutableStateOf(false) }

    fun refresh() {
        scope.launch {
            try {
                notebooks = Api.notebooks()
                error = null
            } catch (e: Exception) {
                error = e.message
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(Unit) { refresh() }
    rememberAutoRefresh { refresh() }

    Box(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize().padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Notebooks", style = MaterialTheme.typography.titleLarge)
                Spacer(Modifier.weight(1f))
                TextButton(onClick = { refresh() }) { Text("↻", color = Muted) }
            }
            error?.let { ErrorBox(it) { error = null; refresh() } }

            if (notebooks.isEmpty() && !loading) {
                Empty("No notebooks yet — tap ＋ to create one.")
            } else {
                LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    items(notebooks, key = { it.id }) { nb ->
                        NotebookCard(
                            nb = nb,
                            onRun = {
                                scope.launch {
                                    try {
                                        val jid = Api.runNotebook(nb.id)
                                        Toast.makeText(context, "Job #$jid queued", Toast.LENGTH_SHORT).show()
                                        refresh()
                                    } catch (e: Exception) {
                                        Toast.makeText(context, e.message, Toast.LENGTH_LONG).show()
                                    }
                                }
                            },
                        )
                    }
                }
            }
        }

        FloatingActionButton(
            onClick = { showCreate = true },
            containerColor = Sky,
            contentColor = Ink,
            modifier = Modifier.align(Alignment.BottomEnd).padding(20.dp),
        ) { Text("＋", fontSize = 22.sp) }
    }

    if (showCreate) {
        CreateNotebookDialog(
            onDismiss = { showCreate = false },
            onCreated = { id ->
                showCreate = false
                Toast.makeText(context, "Notebook #$id created", Toast.LENGTH_SHORT).show()
                refresh()
            },
        )
    }
}

@Composable
private fun NotebookCard(nb: Notebook, onRun: () -> Unit) {
    Card(colors = CardDefaults.cardColors(containerColor = Surface), shape = RoundedCornerShape(14.dp)) {
        Column(Modifier.fillMaxWidth().padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(nb.title, fontWeight = FontWeight.SemiBold, fontSize = 14.5.sp)
                    Text(nb.slug, color = Muted, fontSize = 11.5.sp)
                }
                StatusBadge(nb.lastJobStatus)
            }
            SpacerH(8)
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(nb.accelerator, color = Violet, fontSize = 11.sp,
                    modifier = Modifier.background(Color(0x14A78BFA), RoundedCornerShape(999.dp)).padding(horizontal = 8.dp, vertical = 2.dp))
                if (!nb.internet) {
                    Text("no internet", color = Muted, fontSize = 11.sp)
                }
                if (nb.schedule.isNotBlank()) {
                    Text("⏱ ${nb.schedule}", color = Muted, fontSize = 11.sp)
                }
                Spacer(Modifier.weight(1f))
                Button(onClick = onRun, contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 14.dp, vertical = 4.dp)) {
                    Text("Run", fontSize = 12.5.sp)
                }
            }
        }
    }
}

@Composable
private fun CreateNotebookDialog(onDismiss: () -> Unit, onCreated: (Int) -> Unit) {
    val scope = rememberCoroutineScope()
    var title by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var accelerator by remember { mutableStateOf("auto") }
    var internet by remember { mutableStateOf(true) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("New notebook") },
        text = {
            Column {
                OutlinedTextField(title, { title = it }, label = { Text("Title") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                SpacerH(10)
                OutlinedTextField(
                    code, { code = it },
                    label = { Text("Code") },
                    modifier = Modifier.fillMaxWidth().heightIn(min = 120.dp),
                )
                SpacerH(10)
                Text("Accelerator", color = Muted, fontSize = 12.sp)
                Column(Modifier.heightIn(max = 180.dp).verticalScroll(rememberScrollState())) {
                    Api.ACCELERATORS.forEach { acc ->
                        Row(
                            Modifier.fillMaxWidth().clickable { accelerator = acc },
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            RadioButton(selected = accelerator == acc, onClick = { accelerator = acc })
                            Text(acc, fontSize = 13.sp)
                        }
                    }
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = internet, onCheckedChange = { internet = it })
                    Text("Internet / dataset access", fontSize = 13.sp)
                }
                error?.let { Text(it, color = Red, fontSize = 12.5.sp) }
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    if (title.isBlank()) { error = "Title is required"; return@TextButton }
                    busy = true
                    error = null
                    scope.launch {
                        try {
                            onCreated(Api.createNotebook(title.trim(), code, accelerator, internet))
                        } catch (e: Exception) {
                            error = e.message
                        } finally {
                            busy = false
                        }
                    }
                },
                enabled = !busy,
            ) { Text(if (busy) "…" else "Create") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}
