package com.kaggle.manager

import android.content.Context
import java.io.File

/**
 * Stores the `kc_session` cookie in an app-private file.
 *
 * The file lives in filesDir (MODE_PRIVATE, sandboxed). We deliberately avoid
 * androidx.security-crypto (deprecated/archived) — a custom Keystore layer
 * would be more code for no meaningful gain on a single-owner app.
 */
class SessionStore(context: Context) {
    private val file = File(context.filesDir, "session_cookie")

    fun cookie(): String? =
        if (file.exists()) file.readText().trim().ifEmpty { null } else null

    fun save(cookieHeader: String) {
        file.writeText(cookieHeader)
    }

    fun clear() {
        file.delete()
    }
}
