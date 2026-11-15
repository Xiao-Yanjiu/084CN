$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$framework=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
$refs=@('System.Web.Extensions.dll','System.Net.Http.dll','System.IO.Compression.dll','System.IO.Compression.FileSystem.dll','System.Windows.Forms.dll','System.Xaml.dll') | ForEach-Object { '/r:'+(Join-Path $framework $_) }
$refs+=@('PresentationFramework.dll','PresentationCore.dll','WindowsBase.dll') | ForEach-Object { '/r:'+(Join-Path $framework ('WPF\'+$_)) }
& (Join-Path $framework 'csc.exe') /nologo /target:winexe /optimize+ ('/out:'+(Join-Path $root '084CN.exe')) @refs (Join-Path $PSScriptRoot '084CN.cs') (Join-Path $PSScriptRoot 'NativeInstaller.cs') (Join-Path $PSScriptRoot 'RangeDownload.cs')
if($LASTEXITCODE -ne 0){throw '编译失败'}
'编译完成'
