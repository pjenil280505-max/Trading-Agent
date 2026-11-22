# OkHttp ships its own rules; zxing-android-embedded needs none beyond defaults.
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**
# The QR scanner cannot be exercised in CI (no camera), so keep it exactly as its authors ship it.
-keep class com.journeyapps.barcodescanner.** { *; }
-keep class com.google.zxing.** { *; }
