$ErrorActionPreference = 'Stop'

$packageArgs = @{
    packageName    = 'riplox'
    fileType       = 'exe'
    url64bit       = 'https://github.com/xniperbuilds/riplox-desktop/releases/download/v1.7.0/Riplox_Setup_v1.7.0.exe'
    checksum64     = 'BEE8BCE6332FA3D39A5590F2A20F21EFC6C7C1A2C8B4EC8398C6BA036FF28E03'
    checksumType64 = 'sha256'
    # Inno Setup 6. installer.iss sets PrivilegesRequired=lowest, so this lands
    # in the user's profile without an admin prompt.
    silentArgs     = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-'
    validExitCodes = @(0)
}

Install-ChocolateyPackage @packageArgs
