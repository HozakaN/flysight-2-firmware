// Headless check of FlySightApi on the JVM (the Kotlin and JNA layers SkyGames uses), outside SkyGames.
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "fs-jvm-probe"

// FLYSIGHT_API_DIR: where FlySightApi is checked out, inside SkyGames (it takes SkyGames' version catalog)
val flySightApi = System.getenv("FLYSIGHT_API_DIR")
    ?: (System.getProperty("user.home") + "/AndroidStudioProjects/skygames/Libs/FlySightApi")

includeBuild(flySightApi) {
    dependencySubstitution {
        substitute(module("FlySightApi:api")).using(project(":api"))
        substitute(module("FlySightApi:ble")).using(project(":ble"))
    }
}
