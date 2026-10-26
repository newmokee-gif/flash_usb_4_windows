/* Compare the Python bridge with native DiskArbitration claim callbacks.
 * Invoked only by smoke_raw_macos.py after private virtual-image validation.
 * This helper never opens, mounts, formats or writes the supplied device.
 */
#include <CoreFoundation/CoreFoundation.h>
#include <DiskArbitration/DiskArbitration.h>
#include <ctype.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

struct probe {
    const char *bsd_name;
    int done;
    int claimed;
    DAReturn status;
};

static DADissenterRef release_claim(DADiskRef disk, void *context) {
    (void)disk;
    (void)context;
    return DADissenterCreate(kCFAllocatorDefault, kDAReturnBusy, NULL);
}

static DADissenterRef approve_mount(DADiskRef disk, void *context) {
    struct probe *state = context;
    DADiskRef whole = DADiskCopyWholeDisk(disk);
    if (!whole) {
        return DADissenterCreate(kCFAllocatorDefault, kDAReturnBusy, NULL);
    }
    const char *name = DADiskGetBSDName(whole);
    int matches = name && strcmp(name, state->bsd_name) == 0;
    CFRelease(whole);
    return matches ? DADissenterCreate(kCFAllocatorDefault, kDAReturnBusy, NULL) : NULL;
}

static void claimed(DADiskRef disk, DADissenterRef dissenter, void *context) {
    (void)disk;
    struct probe *state = context;
    state->status = dissenter ? DADissenterGetStatus(dissenter) : 0;
    state->claimed = dissenter == NULL;
    state->done = 1;
}

int main(int argc, char **argv) {
    if (argc != 2 || strncmp(argv[1], "/dev/disk", 9) != 0 || !argv[1][9]) {
        return 2;
    }
    for (const char *p = argv[1] + 9; *p; ++p) {
        if (!isdigit((unsigned char)*p)) {
            return 2;
        }
    }
    struct probe state = {argv[1] + 5, 0, 0, 0};
    DASessionRef session = DASessionCreate(kCFAllocatorDefault);
    if (!session) {
        puts("Native claim probe: DASessionCreate failed");
        return 1;
    }
    DADiskRef disk = DADiskCreateFromBSDName(kCFAllocatorDefault, session, argv[1]);
    if (!disk) {
        puts("Native claim probe: disk lookup failed");
        CFRelease(session);
        return 1;
    }
    CFDictionaryRef description = DADiskCopyDescription(disk);
    printf("Native claim probe: disk description available=%d\n", description != NULL);
    if (description) {
        CFRelease(description);
    }
    CFRunLoopRef loop = CFRunLoopGetCurrent();
    DASessionScheduleWithRunLoop(session, loop, kCFRunLoopDefaultMode);
    DARegisterDiskMountApprovalCallback(session, NULL, approve_mount, &state);
    DADiskClaim(disk, kDADiskClaimOptionDefault, release_claim, &state, claimed, &state);
    CFAbsoluteTime started = CFAbsoluteTimeGetCurrent();
    CFAbsoluteTime deadline = started + 30;
    SInt32 last_result = 0;
    unsigned iterations = 0;
    while (!state.done && CFAbsoluteTimeGetCurrent() < deadline) {
        last_result = CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.05, false);
        ++iterations;
        if (last_result == kCFRunLoopRunFinished) {
            break;
        }
    }
    printf("Native claim probe: done=%d claimed=%d status=0x%08x runloop=%d iterations=%u elapsed=%.3fs\n",
           state.done, state.claimed, (uint32_t)state.status, (int)last_result, iterations,
           CFAbsoluteTimeGetCurrent() - started);
    if (state.claimed) {
        DADiskUnclaim(disk);
    }
    DAUnregisterCallback(session, approve_mount, &state);
    DASessionUnscheduleFromRunLoop(session, loop, kCFRunLoopDefaultMode);
    CFRelease(disk);
    CFRelease(session);
    return state.done && state.claimed ? 0 : 1;
}
