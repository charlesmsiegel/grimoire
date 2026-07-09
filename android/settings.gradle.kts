pluginManagement {
    repositories {
        google()
        mavenCentral()
        // Chaquopy releases are on Maven Central; this is the fallback for
        // pre-release builds only.
        maven(url = "https://chaquo.com/maven")
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        maven(url = "https://chaquo.com/maven")
    }
}
rootProject.name = "grimoire-android"
include(":app")
