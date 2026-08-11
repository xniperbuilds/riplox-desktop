# Build Riplox Send.
#
# Straight through the SDK tools rather than Gradle. The app is four classes
# and one layout with no libraries at all, and the Android Gradle Plugin would
# pull several hundred megabytes of Maven to produce the same APK.
#
# aapt2 -> javac -> d8 -> zip -> zipalign -> apksigner.

$ErrorActionPreference = "Stop"

# Everything is found rather than written down: this file is public, and a
# path with someone's user name in it is both a leak and a build that only
# works on one machine.
$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }

# Override either of these in the environment if the SDK lives elsewhere.
$sdk = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT }
       elseif ($env:ANDROID_HOME) { $env:ANDROID_HOME }
       else { Join-Path $env:LOCALAPPDATA "Android\Sdk" }
$jdk = if ($env:JAVA_HOME) { $env:JAVA_HOME } else { "" }

if (-not (Test-Path $sdk)) {
    throw "Android SDK not found at '$sdk'. Set ANDROID_SDK_ROOT and run again."
}
if (-not $jdk -or -not (Test-Path "$jdk\bin\javac.exe")) {
    throw "JDK 17 not found. Set JAVA_HOME and run again."
}

# Whatever build-tools and platform are installed, newest first, so an SDK
# update does not silently break the build with a version pinned in here.
$tools = Get-ChildItem "$sdk\build-tools" -Directory |
    Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty FullName
$plat = Get-ChildItem "$sdk\platforms" -Directory -Filter "android-*" |
    Sort-Object { [int]($_.Name -replace 'android-', '') } -Descending |
    Select-Object -First 1 | ForEach-Object { Join-Path $_.FullName "android.jar" }
if (-not $tools -or -not $plat) { throw "No build-tools or platform in '$sdk'." }

# One place to bump a version: the manifest. The APK is named from it and
# build\pack_for_relay.py reads the same two attributes, so the built file,
# the relay's /app.json and the app's own idea of itself cannot drift apart -
# and the in-app updater compares versionCode, so a stale copy of it anywhere
# would mean an update that either never appears or never stops appearing.
[xml]$manifest = Get-Content -Raw "$root\AndroidManifest.xml"
$ns = "http://schemas.android.com/apk/res/android"
$versionName = $manifest.manifest.GetAttribute("versionName", $ns)
$versionCode = $manifest.manifest.GetAttribute("versionCode", $ns)
if (-not $versionName -or -not $versionCode) { throw "no version in AndroidManifest.xml" }

$work = "$root\build\_work"
$out  = "$root\dist"
Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $work, "$work\gen", "$work\classes", "$work\dex", $out | Out-Null

function Step($name) { Write-Output ""; Write-Output ("=== " + $name + " ===") }

# An argument file for javac or d8 must be UTF-8 with NO byte-order mark -
# Set-Content -Encoding utf8 writes one, and the tool then reads the BOM as
# part of the first argument ("invalid flag: <BOM>C:\Users..."). Backslashes
# are escape characters inside these files as well, so paths go in with
# forward slashes and quotes around the spaces.
function Write-ArgFile($path, $items) {
    $lines = $items | ForEach-Object { '"' + ($_ -replace '\\', '/') + '"' }
    [System.IO.File]::WriteAllLines($path, $lines,
        (New-Object System.Text.UTF8Encoding $false))
}

# -- signing ----------------------------------------------------------------
# The release key when it is there, the Android debug key when it is not.
#
# Which one signed a build matters more than it looks: Android identifies an
# app by its key, so a phone holding a debug-signed copy refuses a release-
# signed update outright. The build says which one it used, every time.
# Point RIPLOXSEND_KEYSTORE at a keystore and RIPLOXSEND_KEYSTORE_PASS at a
# file holding its password to sign a release build. Neither the key nor its
# location belongs in a public repository, so nothing here has a default.
$release = $env:RIPLOXSEND_KEYSTORE
$releasePass = $env:RIPLOXSEND_KEYSTORE_PASS

if ($release -and $releasePass -and (Test-Path $release) -and (Test-Path $releasePass)) {
    $ks = $release
    $alias = "riploxsend"
    # file:, never pass: - an argument would put the password in the process
    # list for anything on the machine to read.
    # file:, never pass: - an argument would put the password in the process
    # list. No --key-pass: apksigner reads a password file once and hands the
    # same stream to the next request, so asking twice hits end-of-file. A
    # PKCS12 key password is the store password anyway.
    $ksPass = "file:$releasePass"
    $keyPass = $null
    $signedWith = "RELEASE key"
} else {
    $ks = "$env:USERPROFILE\.android\debug.keystore"
    $alias = "androiddebugkey"
    $ksPass = "pass:android"
    $keyPass = "pass:android"
    $signedWith = "debug key (not for anyone else's phone)"

    if (-not (Test-Path $ks)) {
        Step "debug keystore"
        New-Item -ItemType Directory -Force -Path (Split-Path $ks) | Out-Null
        & "$jdk\bin\keytool.exe" -genkeypair -v -keystore $ks -storepass android `
            -keypass android -alias androiddebugkey -keyalg RSA -keysize 2048 `
            -validity 10950 -dname "CN=Android Debug,O=Android,C=US" 2>&1 | Out-Null
        Write-Output "created"
    }
}

Step "aapt2 compile"
& "$tools\aapt2.exe" compile --dir "$root\res" -o "$work\res.zip"
Write-Output "resources compiled"

Step "aapt2 link"
& "$tools\aapt2.exe" link `
    -o "$work\base.apk" `
    -I $plat `
    --manifest "$root\AndroidManifest.xml" `
    -R "$work\res.zip" `
    --java "$work\gen" `
    --min-sdk-version 24 `
    --target-sdk-version 35 `
    --auto-add-overlay
Write-Output "linked"

Step "javac"
# javac writes its notes to stderr, and PowerShell counts anything on stderr
# from a native tool as a terminating error. A note is not a failure - the
# class count below is what says whether it worked.
$ErrorActionPreference = "Continue"
$sources = @()
$sources += (Get-ChildItem "$root\java" -Recurse -Filter *.java | ForEach-Object { $_.FullName })
$sources += (Get-ChildItem "$work\gen" -Recurse -Filter R.java | ForEach-Object { $_.FullName })
Write-ArgFile "$work\sources.txt" $sources
& "$jdk\bin\javac.exe" -nowarn -source 8 -target 8 -encoding UTF-8 `
    -classpath $plat -d "$work\classes" "@$work\sources.txt" 2>&1 |
    Where-Object { $_ -notmatch "bootstrap class path|source value 8|target value 8|deprecat" }
$classCount = (Get-ChildItem "$work\classes" -Recurse -Filter *.class).Count
Write-Output ("compiled " + $classCount + " classes")
if ($classCount -lt 5) { throw "javac produced almost nothing - look at the output above" }

Step "d8"
# Handed a jar rather than a list of class files. d8's argument files are read
# raw - it does not strip the quotes javac requires - so the two tools want
# opposite things. A jar sidesteps the argument file entirely.
& "$jdk\bin\jar.exe" cf "$work\classes.jar" -C "$work\classes" .
& "$tools\d8.bat" --release --min-api 24 --lib $plat --output "$work\dex" "$work\classes.jar"
Write-Output ("dex: " + [math]::Round((Get-Item "$work\dex\classes.dex").Length / 1KB, 1) + " KB")

Step "package"
Copy-Item "$work\base.apk" "$work\unsigned.apk" -Force
Push-Location "$work\dex"
& "$jdk\bin\jar.exe" uf "$work\unsigned.apk" classes.dex
Pop-Location
Write-Output "dex added"

Step "zipalign + sign"
& "$tools\zipalign.exe" -f -p 4 "$work\unsigned.apk" "$work\aligned.apk"
# Removed first: a signing failure that left yesterday's APK in place reported
# the new key and shipped the old signature. It happened once; not again.
$target = "$out\RiploxSend-v$versionName.apk"
Remove-Item $target -Force -ErrorAction SilentlyContinue

$signArgs = @("sign", "--ks", $ks, "--ks-key-alias", $alias, "--ks-pass", $ksPass)
if ($keyPass) { $signArgs += @("--key-pass", $keyPass) }
$signArgs += @("--v1-signing-enabled", "true", "--v2-signing-enabled", "true",
               "--out", $target, "$work\aligned.apk")
& "$tools\apksigner.bat" @signArgs

if (-not (Test-Path $target)) { throw "apksigner produced nothing - see the output above" }
$apk = Get-Item $target
Write-Output ""
Write-Output ("version: " + $versionName + " (code " + $versionCode + ")")
Write-Output ("signed with: " + $signedWith)
Write-Output ("APK:    " + $apk.FullName)
Write-Output ("size:   {0:N0} KB" -f ($apk.Length / 1KB))
Write-Output ("sha256: " + (Get-FileHash $apk.FullName -Algorithm SHA256).Hash)

Step "verify"
& "$tools\apksigner.bat" verify --print-certs $apk.FullName 2>&1 | Select-Object -First 4
& "$sdk\build-tools\35.0.0\aapt2.exe" dump badging $apk.FullName 2>&1 |
    Select-String "package:|application-label:|uses-permission|launchable-activity" |
    ForEach-Object { $_.Line }
