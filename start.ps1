param([ValidateSet('A','B')][string]$Mode='A',[switch]$Check)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$candidates=@()
if ($env:SUANLIN_PYTHON) { $candidates+=@(@{Exe=$env:SUANLIN_PYTHON;Prefix=@()}) }
foreach ($name in @('py','python')) {
 $command=Get-Command $name -ErrorAction SilentlyContinue
 if ($command) { $candidates+=@(@{Exe=$command.Source;Prefix=$(if($name -eq 'py'){@('-3')}else{@()})}) }
}
$quark=Join-Path $env:LOCALAPPDATA 'Quark\User Data\quark-agent\resources\runtimes\python'
if (Test-Path -LiteralPath $quark) {
 foreach($folder in (Get-ChildItem -LiteralPath $quark -Directory | Sort-Object Name -Descending)) {
  $exe=Join-Path $folder.FullName 'python.exe'
  if(Test-Path -LiteralPath $exe){$candidates+=@(@{Exe=$exe;Prefix=@()})}
 }
}
foreach($candidate in $candidates){
 $prefix=@($candidate.Prefix)
 try {
  & $candidate.Exe @prefix -c 'import sys;sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>$null
  if($LASTEXITCODE -ne 0){continue}
 } catch {continue}
 $arguments=@((Join-Path $PSScriptRoot 'package_launcher.py'),$Mode)
 if($Check){$arguments+='--check'}
 & $candidate.Exe @prefix @arguments
 exit $LASTEXITCODE
}
Write-Host 'Python 3.10+ was not found. Install Python or set SUANLIN_PYTHON to python.exe.'
exit 2
