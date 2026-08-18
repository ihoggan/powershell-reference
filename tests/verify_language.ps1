<#
    verify_language.ps1 -- executes the language claims made in
    sheets/powershell-basics.md.

    WHAT
        tools/check_sheets.py proves every snippet PARSES. It cannot
        prove any of them BEHAVE as the surrounding prose claims:
        `Get-Nonsense -Foo bar` parses perfectly. This file runs the
        behavioural claims and compares them against the stated result.

        Every assertion here is cross-platform and side-effect free --
        no files written, no network, no Windows-only cmdlets -- so it
        runs on the Linux box and on a CI runner.

    WHEN YOU'D REACH FOR IT
        After editing sheets/powershell-basics.md, and from CI. If you
        add a claim to that sheet of the form "X gives Y", add it here.
        A claim nobody has executed is a claim.

    HOW IT FAILS
        Exit 1 with the failing lines listed. Break it on purpose by
        changing any Expected value and confirming it goes red -- an
        assertion file that has only ever passed proves nothing.

    TESTED ON
        pwsh 7.4.6 on Linux x64. 17 assertions.
#>

$script:Failures = 0

function Assert-Claim {
    param(
        [Parameter(Mandatory)] [string] $Claim,
        [Parameter(Mandatory)] [AllowNull()] $Actual,
        [Parameter(Mandatory)] [AllowNull()] $Expected
    )
    $got = "$Actual"
    $want = "$Expected"
    if ($got -ceq $want) {
        '{0}  {1,-46} {2}' -f 'PASS', $Claim, $got
    }
    else {
        $script:Failures++
        '{0}  {1,-46} expected {2}, got {3}' -f 'FAIL', $Claim, $want, $got
    }
}

# --- types and operators ------------------------------------------------

Assert-Claim "'2' + 2 concatenates"        ('2' + 2)               '22'
Assert-Claim "2 + '2' adds"                (2 + '2')               4
Assert-Claim "-eq is case-insensitive"     ('Hello' -eq 'hello')   $true
Assert-Claim "-ceq is case-sensitive"      ('Hello' -ceq 'hello')  $false
Assert-Claim "-eq on an array filters"     (@(1, 2, 3) -eq 2)      2
Assert-Claim "-contains checks membership" (('a', 'b') -contains 'a') $true
Assert-Claim "-in is -contains reversed"   ('a' -in @('a', 'b'))   $true
Assert-Claim "100MB literal"               (100MB)                 104857600

# --- loops and flow -----------------------------------------------------

$text = 'Picard'
$reversed = ''
for ($i = $text.Length - 1; $i -ge 0; $i--) { $reversed += $text[$i] }
Assert-Claim "reverse loop starts at Length-1" $reversed 'draciP'

$squares = foreach ($i in 1..4) { $i * $i }
Assert-Claim "foreach used as an expression" ($squares -join ',') '1,4,9,16'

$every = switch (150) { { $_ -gt 100 } { 'Large' } { $_ -gt 10 } { 'Big' } }
Assert-Claim "switch runs EVERY match" ($every -join ',') 'Large,Big'

$stopped = switch (150) { { $_ -gt 100 } { 'Large'; break } { $_ -gt 10 } { 'Big' } }
Assert-Claim "break stops after one branch" ($stopped -join ',') 'Large'

Assert-Claim "Test-Path returns a bool" ((Test-Path $HOME) -is [bool]) $true

# --- errors -------------------------------------------------------------

$uncaught = try {
    Get-Item -Path '/no/such/path' -ErrorAction SilentlyContinue | Out-Null
    'not caught'
} catch { 'caught' }
Assert-Claim "non-terminating error skips catch" $uncaught 'not caught'

$caught = try {
    Get-Item -Path '/no/such/path' -ErrorAction Stop | Out-Null
    'not caught'
} catch { 'caught' }
Assert-Claim "-ErrorAction Stop makes it catchable" $caught 'caught'

$octet = if ('192.0.2.10' -match '^(\d{1,3})\.(\d{1,3})\.') { $Matches[2] }
Assert-Claim '-match populates $Matches' $octet 0

# --- collections --------------------------------------------------------

Assert-Claim "@() around one result gives Count 1" (@((Get-Item $HOME)).Count) 1

# ------------------------------------------------------------------------

''
if ($script:Failures -gt 0) {
    "$script:Failures claim(s) failed"
    exit 1
}
'all claims verified'
exit 0
