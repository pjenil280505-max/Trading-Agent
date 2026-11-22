package org.openmarketos.app

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** The pairing (addresses, pinned certificate, sign-in token), encrypted with a key kept in the Android Keystore. */
class Store(context: Context) {
    data class Pairing(val urls: List<String>, val fingerprint: String, val token: String, val handedOver: Boolean)

    private val prefs = context.getSharedPreferences("omos", Context.MODE_PRIVATE)

    fun load(): Pairing? {
        val blob = prefs.getString("pairing", null) ?: return null
        return try {
            val o = JSONObject(decrypt(blob))
            val arr = o.getJSONArray("urls")
            Pairing((0 until arr.length()).map { arr.getString(it) }, o.getString("fp"), o.getString("token"),
                o.optBoolean("handedOver", false))
        } catch (e: Exception) {
            null // key lost (e.g. device restore) or damaged: pair again
        }
    }

    fun save(p: Pairing) {
        val o = JSONObject().put("urls", JSONArray(p.urls)).put("fp", p.fingerprint).put("token", p.token)
            .put("handedOver", p.handedOver)
        prefs.edit().putString("pairing", encrypt(o.toString())).apply()
    }

    fun clear() = prefs.edit().remove("pairing").putBoolean("alerts", false).apply()

    var alerts: Boolean
        get() = prefs.getBoolean("alerts", false)
        set(v) = prefs.edit().putBoolean("alerts", v).apply()

    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        gen.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build()
        )
        return gen.generateKey()
    }

    private fun encrypt(plain: String): String {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, key())
        return Base64.encodeToString(c.iv + c.doFinal(plain.toByteArray()), Base64.NO_WRAP)
    }

    private fun decrypt(blob: String): String {
        val raw = Base64.decode(blob, Base64.NO_WRAP)
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, raw, 0, 12))
        return String(c.doFinal(raw, 12, raw.size - 12))
    }

    companion object {
        private const val ALIAS = "omos_pairing"
    }
}
