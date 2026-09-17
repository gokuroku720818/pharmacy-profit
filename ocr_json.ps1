param([string]$ImagePath)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

[void][System.Reflection.Assembly]::LoadWithPartialName('System.Runtime.WindowsRuntime')
[void][Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
[void][Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
[void][Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]

$fullPath = [System.IO.Path]::GetFullPath($ImagePath)
$fileTask = [Windows.Storage.StorageFile]::GetFileFromPathAsync($fullPath)
$asTaskMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { 
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1

$fileTaskGeneric = $asTaskMethod.MakeGenericMethod([Windows.Storage.StorageFile]).Invoke($null, @($fileTask))
$fileTaskGeneric.Wait()
$file = $fileTaskGeneric.Result

$streamTask = $file.OpenAsync([Windows.Storage.FileAccessMode]::Read)
$streamTaskGeneric = $asTaskMethod.MakeGenericMethod([Windows.Storage.Streams.IRandomAccessStream]).Invoke($null, @($streamTask))
$streamTaskGeneric.Wait()
$stream = $streamTaskGeneric.Result

$decoderTask = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)
$decoderTaskGeneric = $asTaskMethod.MakeGenericMethod([Windows.Graphics.Imaging.BitmapDecoder]).Invoke($null, @($decoderTask))
$decoderTaskGeneric.Wait()
$decoder = $decoderTaskGeneric.Result

$bitmapTask = $decoder.GetSoftwareBitmapAsync()
$bitmapTaskGeneric = $asTaskMethod.MakeGenericMethod([Windows.Graphics.Imaging.SoftwareBitmap]).Invoke($null, @($bitmapTask))
$bitmapTaskGeneric.Wait()
$bitmap = $bitmapTaskGeneric.Result

$lang = [Windows.Globalization.Language]::new("ko")
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
$ocrTask = $engine.RecognizeAsync($bitmap)
$ocrTaskGeneric = $asTaskMethod.MakeGenericMethod([Windows.Media.Ocr.OcrResult]).Invoke($null, @($ocrTask))
$ocrTaskGeneric.Wait()
$result = $ocrTaskGeneric.Result

$lines = @()
foreach ($line in $result.Lines) {
    $words = @()
    foreach ($word in $line.Words) {
        $words += @{
            text = $word.Text
            x = [int]$word.BoundingRect.X
            y = [int]$word.BoundingRect.Y
            w = [int]$word.BoundingRect.Width
            h = [int]$word.BoundingRect.Height
        }
    }
    $lines += @{
        text = $line.Text
        words = $words
    }
}

$output = @{
    text = $result.Text
    lines = $lines
}

$output | ConvertTo-Json -Depth 4 -Compress
