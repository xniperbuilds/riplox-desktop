$ErrorActionPreference = 'Stop'

$packageArgs = @{
    packageName    = 'riplox'
    fileType       = 'exe'
    url64bit       = 'https://github.com/xniperbuilds/riplox-desktop/releases/download/v1.6.0/Riplox_Setup_v1.6.0.exe'
    checksum64     = 'A700C193AEE0C85FDF894C480B2265D642E70D63EFCDC69DB0AC8048BD334882'
    checksumType64 = 'sha256'
    # Inno Setup 6. installer.iss sets PrivilegesRequired=lowest, so this lands
    # in the user's profile without an admin prompt.
    silentArgs     = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-'
    validExitCodes = @(0)
}

Install-ChocolateyPackage @packageArgs
