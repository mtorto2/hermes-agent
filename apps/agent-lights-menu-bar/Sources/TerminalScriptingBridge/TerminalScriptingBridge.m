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
        terminal.timeout = 1;

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
