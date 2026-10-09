import ca.flysight.api.core.DesktopApiContext
import ca.flysight.api.core.model.DeviceConnectionState
import ca.flysight.api.core.model.FlySightInfo
import ca.flysight.api.core.tools.LoadingState
import ca.flysight.api.flySight
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.mapNotNull
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.system.exitProcess

private val start = System.currentTimeMillis()

private fun say(message: String) {
    println("PROBE %7.2f %s".format((System.currentTimeMillis() - start) / 1000.0, message))
}

/**
 * usage: probe NAME [SAVED_ID] [STAY_SECONDS]
 * Scans for the FlySight called NAME (or takes SAVED_ID with no scan, as SkyGames does when it
 * starts), connects through FlySightApi, waits for what the library reads on connection, and
 * disconnects. Exit code 0 only if the configuration file was read.
 */
fun main(args: Array<String>) {
    val name = args.getOrElse(0) { "FlySight" }
    val savedId = args.getOrNull(1)?.takeIf { it.isNotBlank() && it != "-" }
    val stay = args.getOrNull(2)?.toLongOrNull() ?: 0L
    var ok = false

    runBlocking {
        val api = flySight(DesktopApiContext())
        val info = if (savedId != null) {
            say("no scan: saved identifier $savedId")
            FlySightInfo(name = name, id = savedId)
        } else {
            withTimeoutOrNull(25_000) {
                api.scanForDevicesStream().mapNotNull { list -> list.firstOrNull { it.name == name } }.first()
            }
        }
        if (info == null) {
            say("RESULT: NOT FOUND")
            return@runBlocking
        }
        say("device '${info.name}' id=${info.id} rssi=${info.rssi}")

        val device = api.createDevice(info)
        if (device == null) {
            say("RESULT: createDevice returned null")
            return@runBlocking
        }
        val t = System.currentTimeMillis()
        val error = device.connect()
        say("connect() -> ${error ?: "OK"} after ${System.currentTimeMillis() - t} ms, state=${device.connectionState.value}")
        if (error != null) {
            say("RESULT: CONNECT FAILED")
            return@runBlocking
        }

        val config = withTimeoutOrNull(60_000) {
            device.rawConfigFile.first { it is LoadingState.Loaded || it is LoadingState.Error }
        }
        when (config) {
            is LoadingState.Loaded -> say("config.txt read: ${config.value.length} characters, ${config.value.lines().size} lines")
            is LoadingState.Error -> say("config.txt: ERROR ${config.error}")
            else -> say("config.txt: not read within 60 s (${device.rawConfigFile.value::class.simpleName})")
        }
        val firmware = withTimeoutOrNull(30_000) {
            device.firmwareVersion.first { it is LoadingState.Loaded || it is LoadingState.Error }
        }
        say("firmware version: " + when (firmware) {
            is LoadingState.Loaded -> firmware.value
            is LoadingState.Error -> "ERROR ${firmware.error}"
            else -> "not read within 30 s"
        })
        val state = withTimeoutOrNull(30_000) {
            device.flySightFile.first { it is LoadingState.Loaded || it is LoadingState.Error }
        }
        say("flysight.txt: " + when (state) {
            is LoadingState.Loaded -> "${state.value.length} characters"
            is LoadingState.Error -> "ERROR ${state.error}"
            else -> "not read within 30 s"
        })
        say("mode=${device.deviceMode.value} battery=${device.batteryLevel.value} state=${device.connectionState.value}")

        if (stay > 0) {
            delay(stay * 1000)
            say("after $stay s: state=${device.connectionState.value}")
        }
        ok = config is LoadingState.Loaded && device.connectionState.value == DeviceConnectionState.Connected

        device.disconnect()
        say("disconnect() done, state=${device.connectionState.value}")
        api.cancel()
        say("RESULT: ${if (ok) "OK" else "FAILED"}")
    }
    exitProcess(if (ok) 0 else 1)
}
