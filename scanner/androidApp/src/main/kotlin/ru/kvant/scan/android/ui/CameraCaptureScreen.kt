package ru.kvant.scan.android.ui

import android.content.Context
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import ru.kvant.scan.capture.GateReading
import ru.kvant.scan.capture.LiveGate
import ru.kvant.scan.protocol.CaptureStep
import java.io.File
import java.util.UUID

/**
 * Живой видоискатель с проверкой кадра до съёмки — как в банковских
 * приложениях при съёмке документов.
 *
 * Поток кадров идёт в [ru.kvant.scan.android.capture.GateFrameAnalyzer],
 * общее ядро (LiveGate) отдаёт одну подсказку («блик — наклоните телефон»,
 * «темно», «держите ровно»). Серия хороших кадров — и телефон снимает сам;
 * кнопка остаётся для ручного спуска. Каждый снятый кадр ложится в очередь
 * медиа (офлайн-первичность), шаг завершается набором local id.
 */
@Composable
fun CameraCaptureScreen(
    step: CaptureStep,
    storageDir: File,
    onShot: (File) -> Unit,
    onDone: (shots: Int) -> Unit,
    onCancel: () -> Unit,
) {
    val context = LocalContext.current
    val lifecycle = LocalLifecycleOwner.current
    val gate = remember(step.id) { LiveGate() }
    var reading by remember { mutableStateOf<GateReading?>(null) }
    var shots by remember { mutableIntStateOf(0) }
    var autoRequested by remember { mutableStateOf(false) }

    val minShots = step.repeat?.min ?: 1
    val maxShots = step.repeat?.max ?: minShots

    val imageCapture = remember { ImageCapture.Builder().build() }

    fun takeShot() {
        val file = File(storageDir, "shot-${UUID.randomUUID()}.jpg")
        val opts = ImageCapture.OutputFileOptions.Builder(file).build()
        imageCapture.takePicture(
            opts, ContextCompat.getMainExecutor(context),
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(res: ImageCapture.OutputFileResults) {
                    shots += 1
                    gate.reset()
                    autoRequested = false
                    onShot(file)
                    if (shots >= maxShots) onDone(shots)
                }
                override fun onError(exc: ImageCaptureException) {
                    // Кадр не записался — серия не сбрасывается, человек
                    // просто жмёт ещё раз; молча терять снимок нельзя.
                    autoRequested = false
                }
            },
        )
    }

    LaunchedEffect(autoRequested) {
        if (autoRequested) takeShot()
    }

    Box(Modifier.fillMaxSize()) {
        CameraPreview(
            context = context,
            lifecycleOwner = lifecycle,
            imageCapture = imageCapture,
            onMetrics = { m ->
                val r = gate.onFrame(m)
                reading = r
                if (r.autoFire && shots < maxShots) autoRequested = true
            },
        )

        // Подсказка — одна, крупно, вверху кадра.
        val hint = reading?.hint
        Surface(
            color = if (hint == null) Color(0xCC1B5E20) else Color(0xCC3E2723),
            shape = RoundedCornerShape(10.dp),
            modifier = Modifier.align(Alignment.TopCenter).padding(top = 24.dp),
        ) {
            Text(
                hint ?: "Кадр хороший — снимаю сам…",
                color = Color.White,
                style = MaterialTheme.typography.titleSmall,
                modifier = Modifier.padding(horizontal = 14.dp, vertical = 8.dp),
            )
        }

        Column(
            Modifier.align(Alignment.BottomCenter).padding(bottom = 24.dp)
                .fillMaxWidth(),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(
                "Кадров: $shots" + if (minShots > 1) " (нужно от $minShots до $maxShots)" else "",
                color = Color.White,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier
                    .background(Color(0x88000000), RoundedCornerShape(6.dp))
                    .padding(horizontal = 10.dp, vertical = 3.dp),
            )
            Box(contentAlignment = Alignment.Center) {
                // Кольцо прогресса автоспуска вокруг кнопки.
                CircularProgressIndicator(
                    progress = { reading?.autoProgress ?: 0f },
                    modifier = Modifier.size(84.dp),
                    color = Color(0xFF81C784),
                )
                Surface(
                    onClick = { if (shots < maxShots) takeShot() },
                    shape = CircleShape,
                    color = Color.White,
                    modifier = Modifier.size(66.dp),
                ) {}
            }
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedButton(onClick = onCancel) { Text("Отмена") }
                if (shots >= minShots) {
                    OutlinedButton(onClick = { onDone(shots) }) { Text("Готово ($shots)") }
                }
            }
        }
    }
}

@Composable
private fun CameraPreview(
    context: Context,
    lifecycleOwner: androidx.lifecycle.LifecycleOwner,
    imageCapture: ImageCapture,
    onMetrics: (ru.kvant.scan.capture.FrameMetrics) -> Unit,
) {
    val previewView = remember { PreviewView(context) }
    DisposableEffect(Unit) {
        val providerFuture = ProcessCameraProvider.getInstance(context)
        providerFuture.addListener({
            val provider = providerFuture.get()
            val preview = Preview.Builder().build()
                .also { it.surfaceProvider = previewView.surfaceProvider }
            val analysis = ImageAnalysis.Builder()
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .build()
                .also {
                    it.setAnalyzer(
                        ContextCompat.getMainExecutor(context),
                        ru.kvant.scan.android.capture.GateFrameAnalyzer(onMetrics),
                    )
                }
            provider.unbindAll()
            provider.bindToLifecycle(
                lifecycleOwner, CameraSelector.DEFAULT_BACK_CAMERA,
                preview, imageCapture, analysis,
            )
        }, ContextCompat.getMainExecutor(context))
        onDispose {
            runCatching { ProcessCameraProvider.getInstance(context).get().unbindAll() }
        }
    }
    AndroidView(factory = { previewView }, modifier = Modifier.fillMaxSize())
}
