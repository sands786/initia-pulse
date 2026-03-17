/// INITIA PULSE - On-Chain Social Layer
module pulse::social {
    use std::string::{Self, String};
    use std::signer;
    use std::vector;
    use initia_std::event;
    use initia_std::block;

    const E_NOT_AUTHORIZED: u64     = 1;
    const E_POST_TOO_LONG: u64      = 2;
    const E_ALREADY_FOLLOWING: u64  = 3;
    const E_NOT_FOLLOWING: u64      = 4;
    const E_PROFILE_NOT_FOUND: u64  = 5;
    const E_SESSION_EXPIRED: u64    = 6;
    const E_SESSION_NOT_ACTIVE: u64 = 7;
    const E_SELF_FOLLOW: u64        = 8;

    const MAX_POST_LENGTH: u64 = 280;
    const SESSION_DURATION: u64 = 86400;

    struct Profile has key {
        owner: address,
        username: String,
        display_name: String,
        bio: String,
        post_count: u64,
        follower_count: u64,
        following_count: u64,
        reputation_score: u64,
        joined_at: u64,
    }
    struct PostStore has key { posts: vector<Post> }
    struct Post has store, drop, copy {
        id: u64, author: address, content: String,
        post_type: String, app_context: String, timestamp: u64,
    }
    struct FollowGraph has key { following: vector<address>, followers: vector<address> }
    struct AutoSession has key {
        session_key: address, expires_at: u64,
        posts_this_session: u64, is_active: bool,
    }

    #[event] struct ProfileCreated has drop, store { owner: address, username: String, timestamp: u64 }
    #[event] struct PostPublished has drop, store { author: address, post_id: u64, post_type: String, timestamp: u64 }
    #[event] struct Followed has drop, store { follower: address, followee: address, timestamp: u64 }
    #[event] struct SessionStarted has drop, store { owner: address, expires_at: u64 }
    #[event] struct ReputationUpdated has drop, store { user: address, new_score: u64 }

    public entry fun create_profile(account: &signer, username: String, display_name: String, bio: String) {
        let addr = signer::address_of(account);
        let (_, ts) = block::get_block_info();
        move_to(account, Profile { owner: addr, username, display_name, bio, post_count: 0, follower_count: 0, following_count: 0, reputation_score: 0, joined_at: ts });
        move_to(account, PostStore { posts: vector::empty() });
        move_to(account, FollowGraph { following: vector::empty(), followers: vector::empty() });
        event::emit(ProfileCreated { owner: addr, username, timestamp: ts });
    }

    public entry fun update_profile(account: &signer, display_name: String, bio: String) acquires Profile {
        let addr = signer::address_of(account);
        assert!(exists<Profile>(addr), E_PROFILE_NOT_FOUND);
        let p = borrow_global_mut<Profile>(addr);
        p.display_name = display_name; p.bio = bio;
    }

    public entry fun start_session(account: &signer, session_key: address) acquires AutoSession {
        let addr = signer::address_of(account);
        let (_, ts) = block::get_block_info();
        let expires = ts + SESSION_DURATION;
        if (exists<AutoSession>(addr)) {
            let s = borrow_global_mut<AutoSession>(addr);
            s.session_key = session_key; s.expires_at = expires; s.posts_this_session = 0; s.is_active = true;
        } else {
            move_to(account, AutoSession { session_key, expires_at: expires, posts_this_session: 0, is_active: true });
        };
        event::emit(SessionStarted { owner: addr, expires_at: expires });
    }

    public entry fun end_session(account: &signer) acquires AutoSession {
        let addr = signer::address_of(account);
        assert!(exists<AutoSession>(addr), E_SESSION_NOT_ACTIVE);
        borrow_global_mut<AutoSession>(addr).is_active = false;
    }

    public entry fun post(account: &signer, content: String, post_type: String, app_context: String) acquires Profile, PostStore {
        publish_post_internal(signer::address_of(account), content, post_type, app_context);
    }

    public entry fun session_post(session_signer: &signer, owner_addr: address, content: String, post_type: String, app_context: String) acquires AutoSession, Profile, PostStore {
        assert!(exists<AutoSession>(owner_addr), E_SESSION_NOT_ACTIVE);
        let session = borrow_global_mut<AutoSession>(owner_addr);
        let (_, ts) = block::get_block_info();
        assert!(session.is_active, E_SESSION_NOT_ACTIVE);
        assert!(ts <= session.expires_at, E_SESSION_EXPIRED);
        assert!(signer::address_of(session_signer) == session.session_key, E_NOT_AUTHORIZED);
        session.posts_this_session = session.posts_this_session + 1;
        publish_post_internal(owner_addr, content, post_type, app_context);
    }

    fun publish_post_internal(author: address, content: String, post_type: String, app_context: String) acquires Profile, PostStore {
        assert!(exists<Profile>(author), E_PROFILE_NOT_FOUND);
        assert!(string::length(&content) <= MAX_POST_LENGTH, E_POST_TOO_LONG);
        let (_, ts) = block::get_block_info();
        let profile = borrow_global_mut<Profile>(author);
        let post_id = profile.post_count;
        profile.post_count = profile.post_count + 1;
        vector::push_back(&mut borrow_global_mut<PostStore>(author).posts, Post { id: post_id, author, content, post_type, app_context, timestamp: ts });
        event::emit(PostPublished { author, post_id, post_type, timestamp: ts });
    }

    public entry fun follow(account: &signer, target: address) acquires FollowGraph, Profile {
        let follower_addr = signer::address_of(account);
        assert!(follower_addr != target, E_SELF_FOLLOW);
        assert!(exists<Profile>(target), E_PROFILE_NOT_FOUND);
        let (_, ts) = block::get_block_info();
        let fg = borrow_global_mut<FollowGraph>(follower_addr);
        assert!(!vector::contains(&fg.following, &target), E_ALREADY_FOLLOWING);
        vector::push_back(&mut fg.following, target);
        vector::push_back(&mut borrow_global_mut<FollowGraph>(target).followers, follower_addr);
        borrow_global_mut<Profile>(follower_addr).following_count = borrow_global_mut<Profile>(follower_addr).following_count + 1;
        borrow_global_mut<Profile>(target).follower_count = borrow_global_mut<Profile>(target).follower_count + 1;
        event::emit(Followed { follower: follower_addr, followee: target, timestamp: ts });
    }

    public entry fun unfollow(account: &signer, target: address) acquires FollowGraph, Profile {
        let follower_addr = signer::address_of(account);
        let fg = borrow_global_mut<FollowGraph>(follower_addr);
        let (found, idx) = vector::index_of(&fg.following, &target);
        assert!(found, E_NOT_FOLLOWING);
        vector::remove(&mut fg.following, idx);
        let tg = borrow_global_mut<FollowGraph>(target);
        let (_, fidx) = vector::index_of(&tg.followers, &follower_addr);
        vector::remove(&mut tg.followers, fidx);
        borrow_global_mut<Profile>(follower_addr).following_count = borrow_global_mut<Profile>(follower_addr).following_count - 1;
        borrow_global_mut<Profile>(target).follower_count = borrow_global_mut<Profile>(target).follower_count - 1;
    }

    public entry fun update_reputation(account: &signer, user: address, new_score: u64) acquires Profile {
        let _ = signer::address_of(account);
        if (exists<Profile>(user)) { borrow_global_mut<Profile>(user).reputation_score = new_score; };
        event::emit(ReputationUpdated { user, new_score });
    }

    #[view]
    public fun get_profile(addr: address): (String, String, String, u64, u64, u64, u64) acquires Profile {
        assert!(exists<Profile>(addr), E_PROFILE_NOT_FOUND);
        let p = borrow_global<Profile>(addr);
        (p.username, p.display_name, p.bio, p.post_count, p.follower_count, p.following_count, p.reputation_score)
    }
    #[view]
    public fun get_post_count(addr: address): u64 acquires Profile {
        if (!exists<Profile>(addr)) return 0;
        borrow_global<Profile>(addr).post_count
    }
    #[view]
    public fun session_valid(owner: address): (bool, u64) acquires AutoSession {
        if (!exists<AutoSession>(owner)) return (false, 0);
        let s = borrow_global<AutoSession>(owner);
        (s.is_active, s.expires_at)
    }
    #[view]
    public fun is_following(follower: address, target: address): bool acquires FollowGraph {
        if (!exists<FollowGraph>(follower)) return false;
        vector::contains(&borrow_global<FollowGraph>(follower).following, &target)
    }
}
