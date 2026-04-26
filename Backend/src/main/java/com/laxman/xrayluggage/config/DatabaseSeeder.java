package com.laxman.xrayluggage.config;

import org.springframework.boot.CommandLineRunner;
import org.springframework.stereotype.Component;

import com.laxman.xrayluggage.model.SystemSettings;
import com.laxman.xrayluggage.repository.SystemSettingsRepository;

@Component
public class DatabaseSeeder implements CommandLineRunner {

    private final SystemSettingsRepository settingsRepo;

    public DatabaseSeeder(SystemSettingsRepository settingsRepo) {
        this.settingsRepo = settingsRepo;
    }

    @Override
    public void run(String... args) {
        if (settingsRepo.count() == 0) {
            settingsRepo.save(new SystemSettings());
        }
    }
}