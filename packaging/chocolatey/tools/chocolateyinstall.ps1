$ErrorActionPreference = 'Stop'

$packageArgs = @{
    packageName    = 'riplox'
    fileType       = 'exe'
    url64bit       = 'https://github.com/xniperbuilds/riplox-desktop/releases/download/v1.3.0/Riplox_Setup_v1.3.0.exe'
    checksum64     = 'EB1D19D1BEE69AE28F1988AD5C8F30CCF45BB502CEA5DEBED8F1DF142B7E86C4'
    checksumType64 = 'sha256'
    # Inno Setup 6. installer.iss sets PrivilegesRequired=lowest, so this lands
    # in the user's profile without an admin prompt.
    silentArgs     = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-'
    validExitCodes = @(0)
}

Install-ChocolateyPackage @packageArgs
