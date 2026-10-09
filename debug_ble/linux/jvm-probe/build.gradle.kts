plugins {
    kotlin("jvm") version "2.4.20"
    application
}

dependencies {
    implementation("FlySightApi:api")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.11.0")
}

kotlin {
    jvmToolchain(21)
}

application {
    mainClass.set("ProbeKt")
}
