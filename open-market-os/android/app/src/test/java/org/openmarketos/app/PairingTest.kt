package org.openmarketos.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.ByteArrayInputStream
import java.security.cert.CertificateException
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate

class PairingTest {
    // two certificates made by the host's own generator (omos.security.tls), with their fingerprints
    private val pemA = """-----BEGIN CERTIFICATE-----
MIIBzzCCAXWgAwIBAgIUM0YJoqxyrhzO4Oc2hDLBiYvZHDYwCgYIKoZIzj0EAwIw
RjEdMBsGA1UEAwwUT3BlbiBNYXJrZXQgT1Mgb24gdm0xJTAjBgNVBAoMHE9wZW4g
TWFya2V0IE9TIChzZWxmLXNpZ25lZCkwHhcNMjYxMTIyMTQwMjI1WhcNMzYxMTE5
MTQwNzI1WjBGMR0wGwYDVQQDDBRPcGVuIE1hcmtldCBPUyBvbiB2bTElMCMGA1UE
CgwcT3BlbiBNYXJrZXQgT1MgKHNlbGYtc2lnbmVkKTBZMBMGByqGSM49AgEGCCqG
SM49AwEHA0IABLe7y+CIx4zgOmufTziO167NDvDeyzUC1XWbDmCzuAJfrBuyDzdI
r7G3Jz3Bi6Lfj5zqbuyAvJr4tBh6CK963USjQTA/MBoGA1UdEQQTMBGCCWxvY2Fs
aG9zdIcEwKgBFDAMBgNVHRMBAf8EAjAAMBMGA1UdJQQMMAoGCCsGAQUFBwMBMAoG
CCqGSM49BAMCA0gAMEUCIQCzDYO2PWJnCGXyFWuXbvRgfYUZogPVuOWxhoH5bY+o
mQIgf//yWPbq0ehI7ie/0p4vvb1FpZ6ROF/IpYGjyBcNDRk=
-----END CERTIFICATE-----"""
    private val fpA = "ff6326543b603b6ebb0aea6c098e125db31719a0dabd6b5edf901690bd187865"  // pragma: allowlist secret (public fingerprint of a throwaway test certificate)
    private val pemB = """-----BEGIN CERTIFICATE-----
MIIByDCCAW+gAwIBAgIUCVDkG0XPLksW9Dvg7tPRQDLeGegwCgYIKoZIzj0EAwIw
RjEdMBsGA1UEAwwUT3BlbiBNYXJrZXQgT1Mgb24gdm0xJTAjBgNVBAoMHE9wZW4g
TWFya2V0IE9TIChzZWxmLXNpZ25lZCkwHhcNMjYxMTIyMTQwMjI1WhcNMzYxMTE5
MTQwNzI1WjBGMR0wGwYDVQQDDBRPcGVuIE1hcmtldCBPUyBvbiB2bTElMCMGA1UE
CgwcT3BlbiBNYXJrZXQgT1MgKHNlbGYtc2lnbmVkKTBZMBMGByqGSM49AgEGCCqG
SM49AwEHA0IABDbQdnLelrA87zG+o72cCUnKEyYuuy4hiWiMySsDT3yZU+QbYaUF
cGOzXZKtgmcgtsxKOOJDks2VoyScTaW417ijOzA5MBQGA1UdEQQNMAuCCWxvY2Fs
aG9zdDAMBgNVHRMBAf8EAjAAMBMGA1UdJQQMMAoGCCsGAQUFBwMBMAoGCCqGSM49
BAMCA0cAMEQCIBTD2Z+DjWZiIqYaHkyyl8xYudaFYjMihcx7PJQVjC4SAiB3PC7d
h34leQtuveotO6KrYOMvJbGB1m/VCMwJYCJoeg==
-----END CERTIFICATE-----"""
    private val fpB = "33bd2d00a6ade04ae0a7774afe9990a65929a4ce96df1521141e93f942282fcf"  // pragma: allowlist secret (public fingerprint of a throwaway test certificate)

    private fun cert(pem: String) = CertificateFactory.getInstance("X.509")
        .generateCertificate(ByteArrayInputStream(pem.toByteArray())) as X509Certificate

    @Test fun fingerprintMatchesTheHost() {
        assertEquals(fpA, Pinning.sha256Hex(cert(pemA).encoded))
        assertEquals(fpA.take(4).uppercase() + " " + fpA.substring(4, 8).uppercase(), Pinning.short(fpA))
        assertTrue(Pinning.matches(cert(pemA).encoded, fpA.uppercase()))
    }

    @Test fun pinnedTrustAcceptsOnlyThePairedCertificate() {
        val tm = Pinning.PinnedTrustManager(fpA)
        tm.checkServerTrusted(arrayOf(cert(pemA)), "ECDHE_ECDSA")
        try {
            tm.checkServerTrusted(arrayOf(cert(pemB)), "ECDHE_ECDSA")
            fail("another certificate must be refused")
        } catch (e: CertificateException) {
            assertTrue(e.message!!.contains(Pinning.short(fpB)))
        }
        try {
            tm.checkServerTrusted(arrayOf(), "ECDHE_ECDSA")
            fail("an empty chain must be refused")
        } catch (e: CertificateException) { }
    }

    @Test fun parsesTheComputersQrLink() {
        val link = PairLink.parse("omos://pair?u=https%3A%2F%2F192.168.1.20%3A8766&u=https%3A%2F%2F10.0.0.5%3A8766&c=7k9mxq2p&f=$fpA")!!
        assertEquals(listOf("https://192.168.1.20:8766", "https://10.0.0.5:8766"), link.urls)
        assertEquals("7K9MXQ2P", link.code)
        assertEquals(fpA, link.fingerprint)
    }

    @Test fun refusesBadLinks() {
        assertNull(PairLink.parse("https://example.com"))
        assertNull(PairLink.parse("omos://pair?u=http%3A%2F%2F192.168.1.20%3A8766&c=ABCDEFGH&f=$fpA"))  // plain http
        assertNull(PairLink.parse("omos://pair?u=https%3A%2F%2F192.168.1.20%3A8766&c=AB&f=$fpA"))      // short code
        assertNull(PairLink.parse("omos://pair?u=https%3A%2F%2F192.168.1.20%3A8766&c=ABCDEFGH&f=xyz"))  // bad fingerprint
        assertNull(PairLink.parse("omos://pair?c=ABCDEFGH&f=$fpA"))                                     // no address
    }

    @Test fun normalizesTypedAddresses() {
        assertEquals("https://192.168.1.20:8766", PairLink.normalize("192.168.1.20"))
        assertEquals("https://192.168.1.20:9000", PairLink.normalize(" https://192.168.1.20:9000/ "))
        assertEquals("https://my-pc.local:8766", PairLink.normalize("my-pc.local:8766"))
        assertNull(PairLink.normalize("http://192.168.1.20:8766"))
        assertNull(PairLink.normalize("192.168.1.20:8766/path"))
        assertNull(PairLink.normalize("user@192.168.1.20:8766"))
        assertNull(PairLink.normalize(""))
    }
}
