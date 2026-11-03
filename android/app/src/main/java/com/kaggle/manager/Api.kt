package com.kaggle.manager

import android.content.Context
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONArray
import org.json.JSONObject

class ApiException(val status: Int, message: String) : Exception(message)

object Api {
    const val BASE_URL = "https://renderxkaggle.onrender.com"
    const val COOKIE_NAME = "kc_session"

    val ACCELERATORS = listOf(
        "auto", "cpu", "gpu_t4", "gpu_t4_x2", "gpu_p100", "gpu_v100",
        "gpu_l4", "gpu_a100", "gpu_h100", "tpu_v3_8",
    )

    private lateinit var session: SessionStore
    private val jsonType = "application/json".toMediaType()

    // Render free tier sleeps after ~15 min idle; cold start is ~1 min.
    private val client by lazy {
        OkHttpClient.Builder()
            .connectTimeout(20, TimeUnit.SECONDS)
            .readTimeout(45, TimeUnit.SECONDS)
            .callTimeout(60, TimeUnit.SECONDS)
            .build()
    }

    // /api/agent/message runs the full LLM loop synchronously (up to 120s).
    private val chatClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(20, TimeUnit.SECONDS)
            .readTimeout(130, TimeUnit.SECONDS)
            .callTimeout(150, TimeUnit.SECONDS)
            .build()
    }

    fun init(context: Context) {
        session = SessionStore(context)
    }

    fun clearSession() {
        session.clear()
    }

    // ---------------------------------------------------------------- models

    data class Job(
        val id: Int,
        val notebookId: Int?,
        val notebookTitle: String?,
        val kind: String,
        val status: String,
        val kaggleStatus: String?,
        val error: String?,
        val createdAt: String?,
        val startedAt: String?,
        val finishedAt: String?,
    )

    data class JobDetail(val job: Job, val logs: List<Pair<String, String>>)

    data class Notebook(
        val id: Int,
        val slug: String,
        val title: String,
        val description: String,
        val accelerator: String,
        val internet: Boolean,
        val schedule: String,
        val lastJobStatus: String?,
        val lastJobId: Int?,
    )

    data class OverviewData(
        val counts: Map<String, Int>,
        val totalJobs: Int,
        val successRate: Double?,
        val notebookCount: Int,
        val activeJob: Job?,
        val engineState: String,
        val pendingApprovals: Int,
        val recentJobs: List<Job>,
    )

    data class Approval(
        val id: Int,
        val tool: String,
        val params: String,
        val reason: String,
        val createdAt: String,
    )

    data class ChatMsg(val role: String, val content: String, val ts: String)

    data class AgentReply(val answer: String?, val pendingApproval: Int?, val message: String?)

    // ---------------------------------------------------------------- plumbing

    private fun build(method: String, path: String, body: JSONObject? = null): Request {
        val b = Request.Builder()
            .url(BASE_URL + path)
            .method(method, body?.toString()?.toRequestBody(jsonType))
        session.cookie()?.let { b.header("Cookie", it) }
        return b.build()
    }

    private fun fail(resp: Response, text: String): Nothing {
        val detail = runCatching { JSONObject(text).optString("detail", text.take(300)) }
            .getOrDefault(text.take(300))
        throw ApiException(resp.code, detail.ifBlank { "HTTP ${resp.code}" })
    }

    private fun exec(client: OkHttpClient, req: Request): JSONObject =
        client.newCall(req).execute().use { resp ->
            val text = resp.body?.string() ?: ""
            if (!resp.isSuccessful) fail(resp, text)
            if (text.isBlank()) JSONObject() else JSONObject(text)
        }

    private fun execArray(client: OkHttpClient, req: Request): JSONArray =
        client.newCall(req).execute().use { resp ->
            val text = resp.body?.string() ?: ""
            if (!resp.isSuccessful) fail(resp, text)
            JSONArray(text)
        }

    private fun parseJob(o: JSONObject) = Job(
        id = o.optInt("id"),
        notebookId = if (o.isNull("notebook_id")) null else o.optInt("notebook_id"),
        notebookTitle = o.optString("notebook_title").ifEmpty { null },
        kind = o.optString("kind"),
        status = o.optString("status"),
        kaggleStatus = o.optString("kaggle_status").ifEmpty { null },
        error = o.optString("error").ifEmpty { null },
        createdAt = o.optString("created_at").ifEmpty { null },
        startedAt = o.optString("started_at").ifEmpty { null },
        finishedAt = o.optString("finished_at").ifEmpty { null },
    )

    // ---------------------------------------------------------------- auth

    suspend fun login(password: String) = withContext(Dispatchers.IO) {
        val req = build("POST", "/api/login", JSONObject().put("password", password))
        client.newCall(req).execute().use { resp ->
            val text = resp.body?.string() ?: ""
            if (!resp.isSuccessful) fail(resp, text)
            val setCookie = resp.headers("Set-Cookie")
                .firstOrNull { it.startsWith("$COOKIE_NAME=") }
                ?: throw ApiException(0, "server did not issue a session cookie")
            session.save(setCookie.substringBefore(";"))
        }
    }

    suspend fun getMe(): Boolean = withContext(Dispatchers.IO) {
        runCatching { exec(client, build("GET", "/api/me")).optBoolean("authenticated", false) }
            .getOrDefault(false)
    }

    suspend fun logout() = withContext(Dispatchers.IO) {
        runCatching { exec(client, build("POST", "/api/logout")) }
        session.clear()
    }

    // ---------------------------------------------------------------- overview

    suspend fun overview(): OverviewData = withContext(Dispatchers.IO) {
        val o = exec(client, build("GET", "/api/overview"))
        val counts = mutableMapOf<String, Int>()
        o.optJSONObject("counts")?.keys()?.forEach { k ->
            counts[k] = o.optJSONObject("counts")!!.optInt(k)
        }
        val recentArr = o.optJSONArray("recent_jobs")
        val recent = if (recentArr == null) emptyList()
        else (0 until recentArr.length()).map { parseJob(recentArr.getJSONObject(it)) }
        OverviewData(
            counts = counts,
            totalJobs = o.optInt("total_jobs"),
            successRate = if (o.isNull("success_rate")) null else o.optDouble("success_rate"),
            notebookCount = o.optInt("notebooks"),
            activeJob = if (o.isNull("active_job")) null else parseJob(o.optJSONObject("active_job")),
            engineState = o.optString("engine_state"),
            pendingApprovals = o.optInt("pending_approvals"),
            recentJobs = recent,
        )
    }

    // ---------------------------------------------------------------- notebooks

    suspend fun notebooks(): List<Notebook> = withContext(Dispatchers.IO) {
        val arr = execArray(client, build("GET", "/api/notebooks"))
        (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            Notebook(
                id = o.optInt("id"),
                slug = o.optString("slug"),
                title = o.optString("title"),
                description = o.optString("description"),
                accelerator = o.optString("accelerator", "auto"),
                internet = o.optInt("internet", 1) == 1,
                schedule = o.optString("schedule"),
                lastJobStatus = o.optString("last_job_status").ifEmpty { null },
                lastJobId = if (o.isNull("last_job_id")) null else o.optInt("last_job_id"),
            )
        }
    }

    suspend fun createNotebook(
        title: String,
        code: String,
        accelerator: String,
        internet: Boolean,
    ): Int = withContext(Dispatchers.IO) {
        val body = JSONObject()
            .put("title", title)
            .put("code", code)
            .put("accelerator", accelerator)
            .put("internet", internet)
        exec(client, build("POST", "/api/notebooks", body)).optInt("id")
    }

    suspend fun runNotebook(id: Int): Int = withContext(Dispatchers.IO) {
        exec(client, build("POST", "/api/notebooks/$id/run", JSONObject())).optInt("job_id")
    }

    // ---------------------------------------------------------------- jobs

    suspend fun jobs(status: String? = null): List<Job> = withContext(Dispatchers.IO) {
        val path = if (status == null) "/api/jobs?limit=200" else "/api/jobs?status=${status}&limit=200"
        val arr = execArray(client, build("GET", path))
        (0 until arr.length()).map { parseJob(arr.getJSONObject(it)) }
    }

    suspend fun job(id: Int): JobDetail = withContext(Dispatchers.IO) {
        val o = exec(client, build("GET", "/api/jobs/$id"))
        val logsArr = o.optJSONArray("logs")
        val logs = if (logsArr == null) emptyList()
        else (0 until logsArr.length()).map { i ->
            val l = logsArr.getJSONObject(i)
            l.optString("ts") to l.optString("line")
        }
        JobDetail(job = parseJob(o), logs = logs)
    }

    suspend fun cancelJob(id: Int): String = withContext(Dispatchers.IO) {
        exec(client, build("POST", "/api/jobs/$id/cancel", JSONObject())).optString("message", "ok")
    }

    suspend fun requeueJob(id: Int) = withContext(Dispatchers.IO) {
        exec(client, build("POST", "/api/jobs/$id/requeue", JSONObject()))
    }

    // ---------------------------------------------------------------- approvals

    suspend fun approvals(): List<Approval> = withContext(Dispatchers.IO) {
        val arr = execArray(client, build("GET", "/api/approvals?status=pending"))
        (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            Approval(
                id = o.optInt("id"),
                tool = o.optString("tool"),
                params = o.opt("params").toString(),
                reason = o.optString("reason"),
                createdAt = o.optString("created_at"),
            )
        }
    }

    suspend fun decideApproval(id: Int, approve: Boolean): String = withContext(Dispatchers.IO) {
        val body = JSONObject().put("approve", approve)
        val o = exec(client, build("POST", "/api/approvals/$id/decide", body))
        o.optJSONObject("agent")?.optString("answer") ?: "decided"
    }

    // ---------------------------------------------------------------- agent

    suspend fun chat(text: String): AgentReply = withContext(Dispatchers.IO) {
        val o = exec(chatClient, build("POST", "/api/agent/message", JSONObject().put("text", text)))
        AgentReply(
            answer = o.optString("answer").ifEmpty { null },
            pendingApproval = if (o.isNull("pending_approval")) null else o.optInt("pending_approval"),
            message = o.optString("message").ifEmpty { null },
        )
    }

    suspend fun history(): List<ChatMsg> = withContext(Dispatchers.IO) {
        val arr = execArray(client, build("GET", "/api/agent/history"))
        (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            ChatMsg(role = o.optString("role"), content = o.optString("content"), ts = o.optString("ts"))
        }
    }
}
