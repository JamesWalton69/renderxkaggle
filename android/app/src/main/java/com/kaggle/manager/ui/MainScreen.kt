package com.kaggle.manager.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.kaggle.manager.Api
import kotlinx.coroutines.launch

enum class Tab(val label: String, val icon: String) {
    Overview("Overview", "📊"),
    Notebooks("Notebooks", "📓"),
    Jobs("Jobs", "☰"),
}

@Composable
fun MainScreen(onLogout: () -> Unit) {
    var tab by remember { mutableStateOf(Tab.Overview) }
    val scope = androidx.compose.runtime.rememberCoroutineScope()

    Scaffold(
        containerColor = Ink,
        topBar = {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Kaggle Cloud Manager", color = Color.White, fontSize = 16.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.weight(1f))
                TextButton(onClick = {
                    scope.launch { runCatching { Api.logout() } }
                    onLogout()
                }) { Text("Logout", color = Muted, fontSize = 13.sp) }
            }
        },
        bottomBar = {
            NavigationBar(containerColor = Surface) {
                Tab.entries.forEach { t ->
                    NavigationBarItem(
                        selected = tab == t,
                        onClick = { tab = t },
                        icon = { Text(t.icon, fontSize = 18.sp) },
                        label = { Text(t.label, fontSize = 10.sp) },
                    )
                }
            }
        },
    ) { pad ->
        Box(Modifier.fillMaxSize().padding(pad)) {
            when (tab) {
                Tab.Overview -> OverviewTab()
                Tab.Notebooks -> NotebooksTab()
                Tab.Jobs -> JobsTab()
            }
        }
    }
}
