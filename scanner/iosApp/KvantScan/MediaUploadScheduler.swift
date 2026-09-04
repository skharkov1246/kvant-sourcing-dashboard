import BackgroundTasks
import Foundation
import shared

/// Фоновая доставка медиа на iOS — двойник MediaUploadWorker (Android).
///
/// BGProcessingTask с requiresNetworkConnectivity: вся логика очереди — в
/// MediaWorker ядра, здесь регистрация задачи, чтение файлов и удаление
/// подтверждённых строго по deletablePaths() (I-1). Идентификатор задачи
/// должен быть объявлен в Info.plist (BGTaskSchedulerPermittedIdentifiers).
enum MediaUploadScheduler {
    static let taskId = "ru.kvant.scan.media-upload"

    static func register(stack: SyncStack) {
        BGTaskScheduler.shared.register(forTaskWithIdentifier: taskId, using: nil) { task in
            handle(task as! BGProcessingTask, stack: stack)
        }
    }

    static func submit() {
        let request = BGProcessingTaskRequest(identifier: taskId)
        request.requiresNetworkConnectivity = true
        request.requiresExternalPower = false
        try? BGTaskScheduler.shared.submit(request)
    }

    private static func handle(_ task: BGProcessingTask, stack: SyncStack) {
        submit()  // перезаявка следующего окна — до начала работы
        let worker = MediaWorker(
            queue: stack.mediaQueue,
            uploader: stack.uploader,
            readFile: { path in
                guard let data = FileManager.default.contents(atPath: path) else { return nil }
                return DataBridgeKt.toByteArray(data as NSData)
            }
        )
        Task {
            let report = try? await worker.runOnce()
            if let deletable = try? await stack.mediaQueue.deletablePaths() {
                for path in deletable {
                    try? FileManager.default.removeItem(atPath: path)
                }
            }
            task.setTaskCompleted(success: (report?.failed ?? 1) == 0)
        }
        task.expirationHandler = {
            // Истекло окно — очередь в согласованном состоянии по построению:
            // недоставленное остаётся PENDING и доедет следующим окном.
            task.setTaskCompleted(success: false)
        }
    }
}
