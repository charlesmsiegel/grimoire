import java.util.Properties

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

// Chaquopy's build-machine Python (distinct from the 3.11 runtime packaged in
// the APK) must be a version the plugin supports — <= 3.12 for Chaquopy 15.
// Resolution: -Pgrimoire.buildPython=... > grimoire.buildPython in
// local.properties (written by scripts/*/android-bootstrap) > plugin default.
val localProps = Properties().apply {
    val f = rootProject.file("local.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}
val buildPythonOverride = (findProperty("grimoire.buildPython") as? String)
    ?: localProps.getProperty("grimoire.buildPython")

chaquopy {
    defaultConfig {
        version = "3.11"
        buildPythonOverride?.let { buildPython(it) }
        pip {
            // Keep in lockstep with backend/pyproject.toml *base* dependencies
            // (the `desktop` extra — uvicorn[standard], tiktoken — is desktop-only;
            // count_tokens falls back to a heuristic without tiktoken).
            //
            // pydantic is pinned to the pure-python 1.10 line: pydantic v2's
            // Rust core has no Android wheel in Chaquopy 15's repository
            // (docs/android-architecture.md §7 risk 1, fallback 2). FastAPI
            // supports both lines, and routes.py is v1/v2-agnostic (_dump).
            install("pydantic==1.10.*")
            install("fastapi>=0.110")
            install("uvicorn>=0.29")
            install("httpx>=0.27")
            install("python-multipart>=0.0.9")
            install("holidays>=0.40")
            install("pyluach>=2.2")
            install("pillow>=10.0")
            install("jinja2>=3.1")
            install("markdown>=3.5")
            install("certifi")
            install("markupsafe>=2.0")
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
