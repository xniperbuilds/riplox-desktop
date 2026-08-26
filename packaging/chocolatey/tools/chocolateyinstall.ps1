$ErrorActionPreference = 'Stop'

$packageArgs = @{
    packageName    = 'riplox'
    fileType       = 'exe'
    url64bit       = 'https://github.com/xniperbuilds/riplox-desktop/releases/download/v1.4.0/Riplox_Setup_v1.4.0.exe'
    checksum64     = '9C9F3328F87D241DDB6FD18CE01943653E169905EA0BB15AA564F6E0F0F8C2D6'
    checksumType64 = 'sha256'
    # Inno Setup 6. installer.iss sets PrivilegesRequired=lowest, so this lands
    # in the user's profile without an admin prompt.
    silentArgs     = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-'
    validExitCodes = @(0)
}

Install-ChocolateyPackage @packageArgs
