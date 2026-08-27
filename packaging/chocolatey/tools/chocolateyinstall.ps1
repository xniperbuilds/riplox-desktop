$ErrorActionPreference = 'Stop'

$packageArgs = @{
    packageName    = 'riplox'
    fileType       = 'exe'
    url64bit       = 'https://github.com/xniperbuilds/riplox-desktop/releases/download/v1.4.1/Riplox_Setup_v1.4.1.exe'
    checksum64     = '3806455A781BA87578072F7045B9F7B48FCE7341E04EA9B01EC9A7D6DCBDFBC6'
    checksumType64 = 'sha256'
    # Inno Setup 6. installer.iss sets PrivilegesRequired=lowest, so this lands
    # in the user's profile without an admin prompt.
    silentArgs     = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-'
    validExitCodes = @(0)
}

Install-ChocolateyPackage @packageArgs
