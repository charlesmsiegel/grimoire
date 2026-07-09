plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

// The APK packages the working-tree backend sources and the freshly built
// frontend bundle — there are no Android-side copies of any grimoire code.
// See docs/android-architecture.md §5.
val repoRoot: File = rootProject.projectDir.parentFile
val frontendDir = File(repoRoot, "frontend")

android {
    namespace = "app.grimoire"
    compileSdk = 34

    defaultConfig {
        applicationId = "app.grimoire"
        minSdk = 26
        // Bumping targetSdk to 35 forces edge-to-edge; the WebView insets need
        // explicit handling first (Phase 2 of docs/android-architecture.md).
        targetSdk = 34
        versionCode = 1
        versionName = "0.0.1"
        ndk {
            // ~all 2026 devices; add ABIs only on demand (APK size)
            abiFilters.add("arm64-v8a")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

chaquopy {
    defaultConfig {
        version = "3.11"
        pip {
            // Keep in lockstep with backend/pyproject.toml *base* dependencies
            // (the `desktop` extra — uvicorn[standard], tiktoken — is desktop-only;
            // count_tokens falls back to a heuristic without tiktoken).
            //
            // If pip fails resolving pydantic-core for Android here, apply the
            // documented fallback (docs/android-architecture.md §7 risk 1):
            // either add a locally built pydantic-core wheel via
            //   options("--find-links", "wheels/")
            // or pin the pure-python line:
            //   install("pydantic==1.10.*")  — routes.py is v1/v2-agnostic (_dump)
            install("fastapi>=0.110")
            install("uvicorn>=0.29")
            install("httpx>=0.27")
            install("python-multipart>=0.0.9")
            install("holidays>=0.40")
            install("pyluach>=2.2")
            install("pillow>=10.0")
            install("jinja2>=3.1")
            install("certifi")
        }
    }
    sourceSets {
        getByName("main") {
            // the real backend, straight from the working tree
            srcDir("../../backend/src")
        }
    }
}

dependencies {
    implementation("androidx.activity:activity-ktx:1.9.0")
    implementation("androidx.core:core-ktx:1.13.1")
}

// ---- assets pipeline: frontend dist + prompt templates into the APK ----

val webAssets = layout.buildDirectory.dir("generated/grimoireAssets")

val buildFrontend = tasks.register<Exec>("buildFrontend") {
    description = "Builds the shared React frontend with Vite"
    workingDir = frontendDir
    val npm = if (System.getProperty("os.name").lowercase().contains("windows")) "npm.cmd" else "npm"
    commandLine(npm, "run", "build")
    inputs.dir(File(frontendDir, "src"))
    inputs.dir(File(frontendDir, "public"))
    inputs.files(File(frontendDir, "package.json"), File(frontendDir, "index.html"))
    outputs.dir(File(frontendDir, "dist"))
}

val stageGrimoireAssets = tasks.register<Sync>("stageGrimoireAssets") {
    description = "Stages frontend dist and templates as APK assets"
    dependsOn(buildFrontend)
    into(webAssets)
    from(File(frontendDir, "dist")) { into("grimoire/frontend") }
    from(File(repoRoot, "templates")) { into("grimoire/templates") }
}

android.sourceSets.getByName("main").assets.srcDir(webAssets)
tasks.named("preBuild") { dependsOn(stageGrimoireAssets) }
