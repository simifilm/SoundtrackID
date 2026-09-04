// shazamkit-match: identify a WAV file against Apple's Shazam catalog via ShazamKit.
//
// Usage:  shazamkit-match <path-to-wav>
// Output: a single JSON object on stdout, one of:
//   {"title": "...", "artist": "...", "isrc": "...", "appleMusicURL": "...", "shazamID": "..."}
//   {"result": null}                         // no match
//   {"error": "<message>", "code": <int>}    // failure (code 202 == missing ShazamKit entitlement)
//
// ShazamKit catalog matching requires the `com.apple.developer.shazamkit`
// entitlement, honored only when the binary is signed with a provisioning
// profile from an App ID that has the ShazamKit capability enabled. Without it,
// SHSession returns error 202 (matchAttemptFailed). The Python client treats a
// non-zero/empty result as "unavailable" and falls back gracefully.

import AVFoundation
import Foundation
import ShazamKit

// Longest signature we submit. ShazamKit rejects over-long signatures (error 201,
// its ceiling is ~12s); 10s plus a one-buffer overshoot stays safely under it and
// is plenty to identify a cue.
let MAX_SECONDS = 10.0

func emit(_ obj: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: obj),
       let s = String(data: data, encoding: .utf8) {
        print(s)
    }
    fflush(stdout)
}

func fail(_ message: String, code: Int = -1) -> Never {
    emit(["error": message, "code": code])
    exit(0)
}

let args = CommandLine.arguments
guard args.count > 1 else { fail("usage: shazamkit-match <wav>") }
let url = URL(fileURLWithPath: args[1])

guard let file = try? AVAudioFile(forReading: url) else { fail("cannot read audio file") }

// Convert to a canonical 44.1 kHz mono float format for a strong, valid signature,
// regardless of the source rate / channel count.
let inFormat = file.processingFormat
guard let outFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32,
                                    sampleRate: 44100, channels: 1, interleaved: false),
      let converter = AVAudioConverter(from: inFormat, to: outFormat) else {
    fail("cannot set up audio conversion")
}

let generator = SHSignatureGenerator()
let framesPerRead: AVAudioFrameCount = 16384
let maxOutputFrames = AVAudioFramePosition(MAX_SECONDS * outFormat.sampleRate)
var producedFrames: AVAudioFramePosition = 0
var reachedEnd = false

while !reachedEnd && producedFrames < maxOutputFrames {
    guard let inBuffer = AVAudioPCMBuffer(pcmFormat: inFormat, frameCapacity: framesPerRead) else { break }
    do {
        try file.read(into: inBuffer)
    } catch {
        break
    }
    if inBuffer.frameLength == 0 { break }

    let ratio = outFormat.sampleRate / inFormat.sampleRate
    let outCapacity = AVAudioFrameCount(Double(inBuffer.frameLength) * ratio) + 1024
    guard let outBuffer = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: outCapacity) else { break }

    var fed = false
    var convError: NSError?
    let status = converter.convert(to: outBuffer, error: &convError) { _, inputStatus in
        if fed {
            inputStatus.pointee = .noDataNow
            return nil
        }
        fed = true
        inputStatus.pointee = .haveData
        return inBuffer
    }
    if status == .error || convError != nil {
        break
    }
    if outBuffer.frameLength > 0 {
        do {
            try generator.append(outBuffer, at: nil)
        } catch {
            fail("signature append failed: \(error.localizedDescription)")
        }
        producedFrames += AVAudioFramePosition(outBuffer.frameLength)
    }
    if inBuffer.frameLength < framesPerRead {
        reachedEnd = true
    }
}

let signature = generator.signature()
if ProcessInfo.processInfo.environment["SHAZAM_DEBUG"] != nil {
    FileHandle.standardError.write("sig duration=\(signature.duration)s producedFrames=\(producedFrames)\n".data(using: .utf8)!)
}

final class MatchDelegate: NSObject, SHSessionDelegate {
    func session(_ session: SHSession, didFind match: SHMatch) {
        guard let item = match.mediaItems.first else {
            emit(["result": NSNull()])
            exit(0)
        }
        emit([
            "title": item.title ?? NSNull(),
            "artist": item.artist ?? NSNull(),
            "isrc": item.isrc ?? NSNull(),
            "appleMusicURL": item.appleMusicURL?.absoluteString ?? NSNull(),
            "shazamID": item.shazamID ?? NSNull(),
        ])
        exit(0)
    }

    func session(_ session: SHSession, didNotFindMatchFor signature: SHSignature, error: Error?) {
        if let error = error as NSError? {
            emit(["error": error.localizedDescription, "code": error.code])
        } else {
            emit(["result": NSNull()])
        }
        exit(0)
    }
}

let delegate = MatchDelegate()
let session = SHSession()
session.delegate = delegate
session.match(signature)

// Guard against a hung network match so the subprocess never blocks the pipeline.
DispatchQueue.global().asyncAfter(deadline: .now() + 20) {
    fail("timed out waiting for ShazamKit match", code: -2)
}
RunLoop.main.run()
