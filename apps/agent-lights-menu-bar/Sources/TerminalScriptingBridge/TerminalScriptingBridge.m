#import "TerminalScriptingBridge.h"
@import ScriptingBridge;

NSString * _Nullable AgentLightsTerminalTabSnapshot(pid_t processIdentifier) {
    if (processIdentifier <= 0) {
        return nil;
    }

    @try {
        // A PID-targeted bridge cannot launch Terminal if this process has exited.
        SBApplication *terminal = [SBApplication applicationWithProcessIdentifier:processIdentifier];
        if (terminal == nil) {
            return nil;
        }
        // SBApplication.timeout is in ticks (1/60 s): 1 meant ~17 ms, which
        // dropped tab titles whenever Terminal was briefly busy.
        terminal.timeout = 60;

        NSMutableArray<NSString *> *records = [NSMutableArray array];
        SBElementArray *windows = [terminal elementArrayWithCode:'cwin'];
        for (SBObject *window in windows) {
            SBElementArray *tabs = [window elementArrayWithCode:'ttab'];
            for (SBObject *tab in tabs) {
                NSString *tty = [[tab propertyWithCode:'ttty'] get];
                if (tty.length == 0) {
                    continue;
                }
                NSNumber *displaysCustomTitle = [[tab propertyWithCode:'tdct'] get];
                NSString *customTitle = [[tab propertyWithCode:'titl'] get] ?: @"";
                NSString *windowTitle = [[window propertyWithCode:'pnam'] get] ?: @"";
                [records addObject:[@[tty, displaysCustomTitle.boolValue ? @"true" : @"false", customTitle, windowTitle] componentsJoinedByString:@"\t"]];
            }
        }
        return [records componentsJoinedByString:@"\n"];
    } @catch (NSException *exception) {
        return nil;
    }
}
