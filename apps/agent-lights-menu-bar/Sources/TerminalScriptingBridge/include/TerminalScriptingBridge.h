#import <Foundation/Foundation.h>
#import <sys/types.h>

NS_ASSUME_NONNULL_BEGIN

/// Reads Terminal tab metadata from a specific, already-running process.
/// Returns nil if that process exits or cannot answer the request.
FOUNDATION_EXPORT NSString * _Nullable AgentLightsTerminalTabSnapshot(pid_t processIdentifier);

NS_ASSUME_NONNULL_END
