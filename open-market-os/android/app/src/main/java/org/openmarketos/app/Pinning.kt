package org.openmarketos.app

import android.annotation.SuppressLint
import okhttp3.OkHttpClient
import java.io.IOException
import java.net.InetSocketAddress
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.X509TrustManager

/**
 * The computer's certificate is made on the computer itself, so no certificate authority vouches
 * for it. Instead the app remembers its SHA-256 fingerprint at pairing and accepts that certificate
 * and nothing else, so another device on the network cannot pose as the computer.
 */
object Pinning {
    fun sha256Hex(der: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(der).joinToString("") { "%02x".format(it) }

    /** The short form people compare by eye: first 8 hex digits, as two groups (matches the computer). */
    fun short(fp: String): String = (fp.take(4) + " " + fp.drop(4).take(4)).uppercase()

    fun matches(der: ByteArray?, pinned: String): Boolean =
        der != null && MessageDigest.isEqual(sha256Hex(der).toByteArray(), pinned.lowercase().toByteArray())

    // Not "trust all": accepts exactly one certificate, the one pinned at pairing.
    @SuppressLint("CustomX509TrustManager")
    class PinnedTrustManager(private val pinned: String) : X509TrustManager {
        override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) =
            throw CertificateException("client certificates are not used")

        override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
            val leaf = chain?.firstOrNull() ?: throw CertificateException("no certificate")
            if (!matches(leaf.encoded, pinned)) {
                throw CertificateException("certificate ${short(sha256Hex(leaf.encoded))} is not the paired computer's")
            }
        }

        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }

    fun client(pinned: String, readTimeoutSeconds: Long = 20): OkHttpClient {
        val trust = PinnedTrustManager(pinned)
        val ctx = SSLContext.getInstance("TLS")
        ctx.init(null, arrayOf(trust), SecureRandom())
        return OkHttpClient.Builder()
            .sslSocketFactory(ctx.socketFactory, trust)
            .hostnameVerifier { _, _ -> true } // identity is the pinned certificate, not a host name
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(readTimeoutSeconds, TimeUnit.SECONDS)
            .pingInterval(25, TimeUnit.SECONDS)
            .build()
    }

    /** The fingerprint of the certificate a server presents. Only the TLS handshake happens: nothing is sent. */
    fun fetchFingerprint(host: String, port: Int): String {
        var leaf: X509Certificate? = null
        // Records the certificate so the person can compare it with the computer's screen; the socket is
        // closed right after the handshake, before any data is sent, and nothing is trusted afterwards.
        @SuppressLint("CustomX509TrustManager")
        val capture = object : X509TrustManager {
            override fun checkClientTrusted(c: Array<out X509Certificate>?, a: String?) = throw CertificateException()
            override fun checkServerTrusted(c: Array<out X509Certificate>?, a: String?) {
                leaf = c?.firstOrNull()
            }
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        }
        val ctx = SSLContext.getInstance("TLS")
        ctx.init(null, arrayOf(capture), SecureRandom())
        (ctx.socketFactory.createSocket() as SSLSocket).use { s ->
            s.connect(InetSocketAddress(host, port), 5000)
            s.soTimeout = 5000
            s.startHandshake()
        }
        return sha256Hex(leaf?.encoded ?: throw IOException("the computer sent no certificate"))
    }
}
