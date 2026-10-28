import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

dependencies {
    testImplementation("junit:junit:4.13.2")
}

// The APK packages the working-tree backend sources and the freshly built
// frontend bundle — there are no Android-side copies of any grimoire code.
// See docs/reference/android.md.
val repoRoot: File = rootProject.projectDir.parentFile
val frontendDir = File(repoRoot, "frontend")

// Match compiled_build's boundary: documentation must neither invalidate the
// source stamp nor enter the APK. Real Markdown styles and module data remain.
// Chaquopy 17 forwards getExcludes() into its copy action, losing predicate
// specs. String patterns keep packaged files consistent with declared inputs.
val nonRuntimePatterns = listOf("**/docs/**", "**/README.*", "**/readme.*", "**/__pycache__/**")

android {
    namespace = "app.grimoire"
    compileSdk = 34

    defaultConfig {
        applicationId = "app.grimoire"
        minSdk = 26
        // Bumping targetSdk to 35 forces edge-to-edge; the WebView insets need
        // explicit handling before the target can be raised.
        targetSdk = 34
        // versionCode is derived from versionName: major*10000 + minor*100 + patch.
        versionCode = 203
        versionName = "0.2.3"
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

// Chaquopy's build-machine Python must be the *same minor version* as the
// runtime packaged in the APK (3.12 below). Chaquopy 15 accepted any supported
// version; 17 requires an exact match and fails the build otherwise.
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
        // Kept in lockstep with buildPython above — Chaquopy 17 requires them to
        // match. 3.12 is also what the desktop backend runs.
        version = "3.12"
        buildPythonOverride?.let { buildPython(it) }
        pip {
            // Keep in lockstep with backend/pyproject.toml *base* dependencies
            // (the `desktop` extra — uvicorn[standard], tiktoken — is desktop-only;
            // count_tokens falls back to a heuristic without tiktoken).
            //
            // pydantic is pinned to the pure-python 1.10 line: pydantic v2's
            // Rust core had no Android wheel in Chaquopy 15's repository when
            // this was pinned. The packaging contract is in docs/reference/android.md.
            // Not re-checked against Chaquopy 17 — lifting the pin
            // would also mean retiring `make check-pydantic1`. FastAPI
            // supports both lines, and the routes package is v1/v2-agnostic (_dump).
            install("pydantic==1.10.*")
            // Upper bound shared with `make check-pydantic1`, which runs the whole
            // backend suite against exactly this set. Without it the CI job would
            // test a known-good FastAPI while the APK resolved a newer one, so the
            // job would no longer prove what it claims. Raise both together.
            install("fastapi>=0.110,<0.116")
            install("uvicorn>=0.29")
            install("httpx>=0.27")
            install("python-multipart>=0.0.9")
            install("holidays>=0.40")
            install("pyluach>=2.2")
            install("pillow>=10.0")
            install("jinja2>=3.1")
            install("markdown>=3.5")
            install("markdown-it-py>=3.0")
            install("certifi")
        }
    }
    sourceSets {
        getByName("main") {
            // the real backend, straight from the working tree
            srcDir("../../backend/src")
            exclude(nonRuntimePatterns)
        }
    }
}

dependencies {
    implementation("androidx.activity:activity-ktx:1.9.0")
    implementation("androidx.core:core-ktx:1.13.1")
}

// ---- assets pipeline: frontend dist + prompt templates into the APK ----

val webAssets = layout.buildDirectory.dir("generated/grimoireAssets")
val compiledStamp = layout.buildDirectory.file("generated/compiledBuild/compiled-build.json")
val runtimeBackend = fileTree(File(repoRoot, "backend/src/grimoire")) { exclude(nonRuntimePatterns) }
val runtimeTemplates = fileTree(File(repoRoot, "templates")) { exclude(nonRuntimePatterns) }

val generateCompiledBuild = tasks.register<Exec>("generateCompiledBuild") {
    description = "Fingerprints backend sources and templates for the compiled cache"
    workingDir = repoRoot
    val source = File(repoRoot, "backend/src/grimoire")
    val templates = File(repoRoot, "templates")
    inputs.files(runtimeBackend, runtimeTemplates)
    inputs.files(File(repoRoot, "backend/scripts/compiled_build.py"),
                 File(source, "compiled_build.py"))
    outputs.file(compiledStamp)
    environment("PYTHONPATH", File(repoRoot, "backend/src").absolutePath +
        File.pathSeparator + File(repoRoot, "backend").absolutePath)
    // This stamp has only source identity. The APK adds its own runtime and
    // distribution versions; build-machine Python must never stand in for it.
    commandLine(buildPythonOverride ?: "python", "-m", "scripts.compiled_build",
        "--package", source.absolutePath, "--templates", templates.absolutePath,
        "--output", compiledStamp.get().asFile.absolutePath)
}

apply(from = rootProject.file("frontend.gradle.kts"))
val buildFrontend = tasks.named("buildFrontend")

val stageGrimoireAssets = tasks.register<Sync>("stageGrimoireAssets") {
    description = "Stages frontend dist, templates and compiled-build stamp as APK assets"
    dependsOn(buildFrontend, generateCompiledBuild)
    into(webAssets)
    from(File(frontendDir, "dist")) { into("grimoire/frontend") }
    from(runtimeTemplates) { into("grimoire/templates") }
    from(compiledStamp) { into("grimoire") }
}

android.sourceSets.getByName("main").assets.srcDir(webAssets)
tasks.named("preBuild") { dependsOn(stageGrimoireAssets) }
