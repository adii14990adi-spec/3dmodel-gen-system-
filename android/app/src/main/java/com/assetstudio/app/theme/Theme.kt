package com.assetstudio.app.theme

import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val Dark = darkColorScheme(
    primary = Color(0xFF5EEAD4), secondary = Color(0xFF7DD3FC),
    background = Color(0xFF0E1117), surface = Color(0xFF161B24),
    onBackground = Color(0xFFE6EDF6), onSurface = Color(0xFFE6EDF6)
)

@Composable
fun StudioTheme(content: @Composable () -> Unit) =
    MaterialTheme(colorScheme = Dark, typography = Typography(), content = content)
