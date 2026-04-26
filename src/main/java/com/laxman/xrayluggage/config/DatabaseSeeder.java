package com.laxman.xrayluggage.config;

import org.springframework.boot.CommandLineRunner;
import org.springframework.stereotype.Component;

import com.laxman.xrayluggage.model.SystemSettings;
import com.laxman.xrayluggage.repository.SystemSettingsRepository;

// CommandLineRunner runs automatically when Spring Boot starts up
// Replaces: async def init_db() in database.py:
//   async with AsyncSessionLocal() as session:
//       if not result.scalar():
//           session.add(SystemSettings())
//           await session.commit()
@Component
public class DatabaseSeeder implements CommandLineRunner {

    private final SystemSettingsRepository settingsRepo;

    public DatabaseSeeder(SystemSettingsRepository settingsRepo) {
        this.settingsRepo = settingsRepo;
    }

    @Override
    public void run(String... args) {
        // If no settings row exists, create one with defaults
        // Replaces the seed block in init_db()
        if (settingsRepo.count() == 0) {
            settingsRepo.save(new SystemSettings());
            System.out.println("✅ Default SystemSettings seeded");
        }
    }
}
