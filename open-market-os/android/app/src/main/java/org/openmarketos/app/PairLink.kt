package org.openmarketos.app

import java.net.URI
import java.net.URLDecoder

/** What the QR code on the computer's Settings → Devices page contains: omos://pair?u=…&u=…&c=CODE&f=SHA256 */
data class PairLink(val urls: List<String>, val code: String, val fingerprint: String?) {
    companion object {
        private val CODE = Regex("^[A-Za-z0-9]{6,16}$")
        private val FP = Regex("^[0-9a-fA-F]{64}$")

        fun parse(text: String): PairLink? {
            val t = text.trim()
            if (!t.startsWith("omos://pair?")) return null
            val params = t.substringAfter('?').split('&').filter { it.contains('=') }.map {
                val (k, v) = it.split('=', limit = 2)
                k to URLDecoder.decode(v, "UTF-8")
            }
            val urls = params.filter { it.first == "u" }.mapNotNull { normalize(it.second) }
            val code = params.firstOrNull { it.first == "c" }?.second ?: return null
            val fp = params.firstOrNull { it.first == "f" }?.second
            if (urls.isEmpty() || !CODE.matches(code) || (fp != null && !FP.matches(fp))) return null
            return PairLink(urls, code.uppercase(), fp?.lowercase())
        }

        /** "192.168.1.20:8766", "https://192.168.1.20:8766/" → "https://192.168.1.20:8766"; plain http is refused. */
        fun normalize(input: String, defaultPort: Int = 8766): String? {
            var s = input.trim().trimEnd('/')
            if (s.isEmpty() || s.startsWith("http://", ignoreCase = true)) return null
            if (!s.startsWith("https://", ignoreCase = true)) s = "https://$s"
            val u = try { URI(s) } catch (e: Exception) { return null }
            val host = u.host ?: return null
            if (host.isEmpty() || !(u.rawPath ?: "").trim('/').isEmpty() || u.rawQuery != null || u.userInfo != null) return null
            val port = if (u.port > 0) u.port else defaultPort
            if (port > 65535) return null
            return "https://$host:$port"
        }
    }
}
