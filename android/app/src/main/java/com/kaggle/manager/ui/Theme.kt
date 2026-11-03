package com.kaggle.manager.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// Palette mirrors the web dashboard (dark-first).
val Ink = Color(0xFF070B14)
val Surface = Color(0xFF0D1320)
val Border = Color(0xFF1E293B)
val Sky = Color(0xFF7DD3FC)
val Amber = Color(0xFFFBBF24)
val Violet = Color(0xFFC4B5FD)
val Green = Color(0xFF34D399)
val Red = Color(0xFFFB7185)
val Muted = Color(0xFF94A3B8)

private val Scheme = darkColorScheme(
    primary = Sky,
    onPrimary = Color(0xFF071018),
    secondary = Violet,
    tertiary = Amber,
    background = Ink,
    onBackground = Color(0xFFE2E8F0),
    surface = Surface,
    onSurface = Color(0xFFE2E8F0),
    surfaceVariant = Color(0xFF111827),
    onSurfaceVariant = Muted,
    error = Red,
    outline = Border,
)

@Composable
fun KaggleTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = Scheme, content = content)
}
