# PowerShell basics

**Covers** — the pipeline, variables and types, comparison operators,
branching and loops, functions, and error handling.

**Reach for it when** — you're writing something longer than a one-liner
and want to get the shape right, or something is behaving oddly and you
suspect the language rather than the cmdlet.

**Verified** — every block parses under PowerShell 7.4.6. These are
cross-platform: the language examples run on Linux and macOS too, only
the Windows-specific cmdlets don't.

---

## Everything is an object

This is the one idea the rest depends on. Other shells pass text between
commands and you pull it apart with `awk` or `cut`. PowerShell passes
*objects*, and you select properties by name.

```powershell
Get-Process | Get-Member
```

`Get-Member` is the most useful command in the language. It tells you
what you're actually holding — the type, its properties, its methods —
which is nearly always the answer when something won't behave.

```powershell
Get-Date | Get-Member -MemberType Property
(Get-Date).Year
(Get-Date).AddDays(-7)
```

Three ways to look at the same thing, each for a different purpose:

```powershell
Get-Process -Name 'pwsh' | Format-Table Name, Id, CPU -AutoSize
Get-Process -Name 'pwsh' | Format-List *
Get-Process -Name 'pwsh' | Select-Object Name, Id, CPU
```

`Format-*` is for your eyes only. It destroys the objects and replaces
them with formatting instructions, so anything after it in the pipeline
gets nonsense:

```powershell
# Wrong -- Export-Csv receives formatting objects, not processes
Get-Process | Format-Table Name, CPU | Export-Csv -Path 'out.csv' -NoTypeInformation

# Right -- format last, or not at all
Get-Process | Select-Object Name, CPU | Export-Csv -Path 'out.csv' -NoTypeInformation
```

Rule of thumb: `Format-*` is the last thing on the line, or it isn't
there.

---

## The pipeline

```powershell
Get-Process | Where-Object { $_.CPU -gt 50 } | Sort-Object CPU -Descending |
    Select-Object -First 5 Name, Id, CPU
```

`$_` is the current object. `$PSItem` is the same thing spelled out, and
reads better inside a long block:

```powershell
1, 2, 3 | ForEach-Object { $PSItem * 2 }
```

`Where-Object` supports a shorthand for simple comparisons, which is
worth knowing because you'll see it everywhere:

```powershell
Get-Process | Where-Object CPU -gt 50
Get-Service | Where-Object Status -eq 'Running'
```

The shorthand only handles one comparison. The moment you need `-and`,
go back to the scriptblock form.

**Filter as early as you can.** Where a cmdlet has its own `-Filter`,
that runs at the source; `Where-Object` runs after everything has already
been fetched:

```powershell
# Filters at the source
Get-ChildItem -Path $HOME -Filter '*.log' -Recurse

# Fetches everything, then discards most of it
Get-ChildItem -Path $HOME -Recurse | Where-Object { $_.Name -like '*.log' }
```

`ForEach-Object` (pipeline) and `foreach` (statement) are different
things with confusingly similar names:

```powershell
Get-ChildItem | ForEach-Object { $_.Length }

$files = Get-ChildItem
foreach ($file in $files) { $file.Length }
```

The statement form is faster and lets you `break`. The pipeline form
starts producing output immediately instead of waiting for the whole
collection, which matters on something slow.

---

## Variables and types

```powershell
$name  = 'Iain'
$count = 42
$ok    = $true
$items = @()
$map   = @{}
```

PowerShell infers the type, which is convenient right up until it isn't:

```powershell
$a = '2'
$b = '2'
$a + $b        # 22 -- string concatenation, not arithmetic
```

The left-hand operand decides. `'2' + 2` is `'22'`; `2 + '2'` is `4`.
Cast when the input might be a string, which is anything from
`Read-Host`, a CSV, or a file:

```powershell
[int]$a = '2'
[int]$b = '2'
$a + $b        # 4
```

`Read-Host` always returns a string, so this is the common trap:

```powershell
[int]$number = Read-Host 'Enter a number'
$number * 2
```

Quoting rules matter more here than in most languages:

```powershell
$total = 4
Write-Host "Total is $total"                   # Total is 4
Write-Host 'Total is $total'                   # Total is $total
Write-Host "Two plus one is $(1 + 2)"          # expression, needs $( )
Write-Host "Path is $($env:USERPROFILE)"       # property access needs it too
```

Single quotes are literal. Double quotes expand. `$( )` is needed for
anything that isn't a bare variable name.

Useful size literals, so you never type the byte count:

```powershell
Get-ChildItem -Path $HOME -Recurse -File | Where-Object { $_.Length -gt 100MB }
```

`KB MB GB TB PB` all work.

Environment and automatic variables:

```powershell
$env:COMPUTERNAME
$env:USERPROFILE
Get-ChildItem env:            # everything in the environment
$PSVersionTable               # which PowerShell you're on
$PSVersionTable.PSVersion.Major
```

`$PSVersionTable.PSVersion.Major` is `5` for Windows PowerShell and `7`
for current PowerShell — the check to make when a script must run on
both.

---

## Comparison operators

Not `==`, `!=`, `>`, `<`. Those either fail or mean something else
entirely — `>` is a redirect.

| Operator | Means |
|---|---|
| `-eq` `-ne` | equal, not equal |
| `-gt` `-ge` `-lt` `-le` | greater, greater-or-equal, less, less-or-equal |
| `-like` `-notlike` | wildcard match (`*`, `?`) |
| `-match` `-notmatch` | regex match |
| `-contains` `-notcontains` | collection contains this item |
| `-in` `-notin` | this item is in that collection |
| `-is` `-isnot` | type test |
| `-and` `-or` `-not` / `!` | boolean logic |

```powershell
'Hello' -eq 'hello'                    # True -- case-insensitive by default
'Hello' -ceq 'hello'                   # False -- 'c' prefix forces case-sensitive
'server01' -like 'server*'             # True
'server01' -match '^\w+\d{2}$'         # True
'a', 'b' -contains 'a'                 # True
'a' -in @('a', 'b')                    # True
(Get-Date) -is [datetime]              # True
```

`-contains` and `-in` are the same test with the operands swapped, which
is the usual reason one of them "doesn't work".

`-match` also populates `$Matches`, which is easy to miss:

```powershell
if ('192.0.2.10' -match '^(\d{1,3})\.(\d{1,3})\.') {
    $Matches[1]
    $Matches[2]
}
```

---

## Branching

```powershell
$path = $HOME

if (Test-Path -Path $path) {
    Write-Host "$path verified"
}
elseif ($path -eq '') {
    Write-Host 'No path given'
}
else {
    Write-Host "$path not found"
}
```

`Test-Path` returns a boolean already, so `if (Test-Path $path)` is
enough — `-eq $true` adds nothing. And a boolean has no ordering, so
`-lt $false` is meaningless; that's a bug, not a style choice.

`switch` beats a stack of `elseif` once there are more than about three
branches:

```powershell
[int]$value = Read-Host 'Enter a number'

switch ($value) {
    1       { 'One' }
    2       { 'Two' }
    { $_ -gt 100 } { 'Large'; break }
    default { "Nothing defined for $value" }
}
```

Two things `switch` does that `if` can't: a scriptblock as a condition,
and — unlike most languages — it evaluates *every* matching branch unless
you `break`. That's occasionally what you want and usually a surprise.

```powershell
switch -Regex ('server01.example.com') {
    '^server'      { 'Starts with server' }
    '\.example\.com$' { 'In the example.com domain' }
}
```

---

## Loops

```powershell
for ($i = 0; $i -lt 5; $i++) { $i }
```

Three parts separated by **semicolons**, and the middle is a comparison
using `-lt`, not a bare expression. `for ($i = 0; $i - 15; $i++)` is
subtraction, not a test, and loops forever.

```powershell
$text     = 'Jean-Luc Picard'
$reversed = ''
for ($i = $text.Length - 1; $i -ge 0; $i--) {
    $reversed += $text[$i]
}
$reversed
```

`$text.Length - 1` — starting at `.Length` reads one past the end and
prepends an empty entry.

```powershell
foreach ($item in 1..5) { $item * $item }
```

`do`/`while` runs at least once; `while` may not run at all. That is the
whole difference, and it decides which one you want when prompting:

```powershell
$verified = $false
do {
    $path = Read-Host 'Enter a path'
    if (Test-Path -Path $path) { $verified = $true }
} while (-not $verified)
```

**Never build an array with `+=` in a loop.** Arrays are fixed size, so
`+=` allocates a new one and copies everything across, every iteration:

```powershell
# Slow -- quadratic. Noticeable by a few thousand items.
$results = @()
foreach ($i in 1..10000) { $results += $i }

# Fast -- a real list
$results = [System.Collections.Generic.List[object]]::new()
foreach ($i in 1..10000) { $results.Add($i) }

# Also fast, and usually the tidiest -- let the loop emit
$results = foreach ($i in 1..10000) { $i }
```

That last form catches people out: a `foreach` used as an expression
collects whatever its body emits. It's the idiomatic way to build a
report.

---

## Functions

```powershell
function Get-Something {
    'result'
}
```

Anything not captured becomes output, including things you didn't mean
to return:

```powershell
function Add-Item {
    $list = [System.Collections.Generic.List[object]]::new()
    $list.Add('a')          # .Add() returns nothing -- fine
    $list
}
```

`$list.Add('a')` on a generic list is silent, but many .NET methods
return a value that lands in your output stream unnoticed. `| Out-Null`
or `$null = ...` suppresses it.

The full form, which is worth writing once and copying:

```powershell
function Get-DiskReport {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory, ValueFromPipeline)]
        [ValidateNotNullOrEmpty()]
        [string[]] $ComputerName,

        [ValidateRange(1, 99)]
        [int] $WarnPercent = 20
    )
    begin {
        Write-Verbose "Threshold is $WarnPercent%"
    }
    process {
        foreach ($computer in $ComputerName) {
            [PSCustomObject]@{
                ComputerName = $computer
                WarnPercent  = $WarnPercent
            }
        }
    }
}
```

- `[CmdletBinding()]` gives you `-Verbose`, `-Debug`, `-ErrorAction` for
  free, and makes `Write-Verbose` work.
- `process { }` is what makes pipeline input work. Without it, only the
  last piped item is seen — a bug that looks like the pipeline dropping
  data.
- Validation attributes are for cheap, local checks. Don't put network
  calls in them: it makes the function slow, untestable offline, and it
  throws instead of reporting.

Emit `[PSCustomObject]` rather than formatted strings. A function that
returns objects can be sorted, filtered and exported; one that returns
`"Server X has 20% free"` can only be read.

---

## Errors

Two kinds. Terminating errors stop execution; non-terminating ones write
to the error stream and carry on. Most cmdlet failures are
non-terminating, which is why `try`/`catch` so often appears not to work:

```powershell
# catch never fires -- the error is non-terminating
try {
    Get-Item -Path '/no/such/path'
}
catch {
    Write-Host 'caught'
}

# catch fires -- -ErrorAction Stop promotes it
try {
    Get-Item -Path '/no/such/path' -ErrorAction Stop
}
catch {
    Write-Host "caught: $($_.Exception.Message)"
}
```

`-ErrorAction Stop` on the call inside the `try` is the missing piece
nine times out of ten.

| `-ErrorAction` | Effect |
|---|---|
| `Continue` | write the error, keep going (the default) |
| `Stop` | terminating — catchable |
| `SilentlyContinue` | suppress, but still record in `$Error` |
| `Ignore` | suppress and don't record |
| `Inquire` | ask |

```powershell
try {
    $content = Get-Content -Path '/no/such/file' -ErrorAction Stop
}
catch [System.IO.FileNotFoundException] {
    Write-Warning 'File not found'
}
catch {
    Write-Error $_
    throw
}
finally {
    Write-Verbose 'Cleanup runs either way'
}
```

Order matters: specific `catch` blocks before the general one. `finally`
runs whether or not anything threw, which is where disconnects and
cleanup belong.

Inspecting what you caught:

```powershell
try {
    Invoke-WebRequest -Uri 'https://example.com/nope' -ErrorAction Stop
}
catch {
    $status = $_.Exception.Response.StatusCode.value__
    switch ($status) {
        404     { Write-Warning 'Not found' }
        401     { Write-Warning 'Unauthorized' }
        429     { Write-Warning 'Throttled' }
        default { throw }
    }
}
```

`$_` inside a `catch` is an `ErrorRecord`, not the exception itself. The
useful bits:

```powershell
try { 1 / 0 }
catch {
    $_.Exception.Message
    $_.CategoryInfo.Category
    $_.InvocationInfo.ScriptLineNumber
    $_.ScriptStackTrace
}
```

`$Error` holds the session's error history, newest first:

```powershell
$Error[0]
$Error.Count
$Error.Clear()
```

Handy at the prompt. Don't build logic on it in a script — it's global
state and anything else in the session writes to it too.

---

## Gotchas

**One result is not an array.** `.Count` on a single object returns 1 in
PowerShell 7 but nothing useful in 5.1. Wrap it:

```powershell
$results = Get-ChildItem -Path $HOME -Filter 'nothing-matches-this'
"Found {0}" -f @($results).Count
```

**Comparison against an array filters instead of comparing.**

```powershell
@(1, 2, 3) -eq 2        # returns 2, not True
```

That's a feature, and it's why `if ($array -eq $value)` can behave
unexpectedly. Use `-contains` when you mean containment.

**`$null` goes on the left.**

```powershell
if ($null -eq $result) { 'nothing' }
```

`$result -eq $null` with `$result` as an array applies the filtering rule
above and returns the empty elements, not a boolean.

**Execution policy is not a security boundary.** If a script won't run:

```powershell
Get-ExecutionPolicy -List
```

Set it once on the machine, or run the file with `-ExecutionPolicy
Bypass`. Putting `Set-ExecutionPolicy RemoteSigned` at the top of a
script needs elevation and changes machine state to fix something the
script can't detect anyway.

**PowerShell 7 and Windows PowerShell 5.1 are separate installs.** `pwsh`
is 7; `powershell.exe` is 5.1. Both can be on the same machine, with
separate module paths and profiles — which is usually the answer to "it
works in my other window".
