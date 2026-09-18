# Runs Maven with a UTF-8 console so a PowerShell 5.1 redirect (mvn ... > run.log)
# keeps non-Latin datasheet values instead of re-decoding the JVM's UTF-8 bytes
# with the console code page. Forwards every argument to mvn unchanged:
#
#   tools\mvn-utf8.ps1 test "-DsuiteXmlFile=Suites/Programaccountregression_Regression.xml" > full-run.log
#
# The JVMs themselves are already UTF-8 (pom argLine + .mvn/jvm.config); this
# only fixes what PowerShell does with their output. cmd.exe needs nothing.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
& mvn @args
exit $LASTEXITCODE
