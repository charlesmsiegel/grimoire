// The frontend build is also executable without Android plugins, so its
// incremental dependency contract can be exercised with a small npm fixture.
val frontendDir = File(rootProject.projectDir.parentFile, "frontend")
val inputInventory = File(frontendDir, "build-inputs.txt")
val bundleInputs = inputInventory.readLines().filter { it.isNotEmpty() }

tasks.register<Exec>("buildFrontend") {
    description = "Builds the shared React frontend with Vite"
    workingDir = frontendDir
    val npm = if (System.getProperty("os.name").lowercase().contains("windows")) "npm.cmd" else "npm"
    commandLine(npm, "run", "build")
    // All hosts build the same bundle. Sharing its input inventory prevents a
    // Vite/TypeScript/lockfile edit from leaving the APK with stale UI bytes.
    inputs.file(inputInventory)
    inputs.files(bundleInputs.map { File(frontendDir, it) })
    outputs.dir(File(frontendDir, "dist"))
}
