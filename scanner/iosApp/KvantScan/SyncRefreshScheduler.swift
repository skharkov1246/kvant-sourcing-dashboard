import BackgroundTasks
import Foundation
import shared

/// Периодический pull на iOS — двойник SyncPullWorker (Android, §09.3):
/// BGAppRefreshTask подтягивает задания, протоколы и справочники в фоне.
/// Данных в push нет по построению — уведомление (когда появится) будет
/// лишь досрочно запускать это же обновление.
enum SyncRefreshScheduler {
    static let taskId = "ru.kvant.scan.sync-pull"

    static func register(stack: SyncStack) {
        BGTaskScheduler.shared.register(forTaskWithIdentifier: taskId, using: nil) { task in
            handle(task as! BGAppRefreshTask, stack: stack)
        }
    }

    static func submit() {
        let request = BGAppRefreshTaskRequest(identifier: taskId)
        request.earliestBeginDate = Date(timeIntervalSinceNow: 15 * 60)
        try? BGTaskScheduler.shared.submit(request)
    }

    private static func handle(_ task: BGAppRefreshTask, stack: SyncStack) {
        submit()  // перезаявка следующего окна — до начала работы
        Task {
            do {
                try await stack.pullOnce()
                task.setTaskCompleted(success: true)
            } catch {
                // Сеть моргнула — прежние данные валидны (§04.1).
                task.setTaskCompleted(success: false)
            }
        }
        task.expirationHandler = { task.setTaskCompleted(success: false) }
    }
}
