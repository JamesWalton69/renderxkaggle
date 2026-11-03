package com.kaggle.manager

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import com.kaggle.manager.ui.KaggleTheme
import com.kaggle.manager.ui.LoginScreen
import com.kaggle.manager.ui.MainScreen

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Api.init(applicationContext)
        enableEdgeToEdge()
        setContent {
            KaggleTheme {
                Root()
            }
        }
    }
}

@Composable
private fun Root() {
    var authed by remember { mutableStateOf<Boolean?>(null) }

    LaunchedEffect(Unit) {
        authed = runCatching { Api.getMe() }.getOrDefault(false)
    }

    when (authed) {
        null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            CircularProgressIndicator()
        }
        false -> LoginScreen(onSuccess = { authed = true })
        true -> MainScreen(onLogout = { authed = false })
    }
}
